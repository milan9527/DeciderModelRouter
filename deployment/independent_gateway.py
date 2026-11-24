"""Create dedicated gateway resources through AWS SDKs; never use CloudFormation."""

import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

NAME = "decider-model-router"
REGION = "us-east-1"
TAGS = [
    {"Key": "Project", "Value": "DeciderModelRouter"},
    {"Key": "ManagedBy", "Value": "DirectAWSAPI"},
]


class Infrastructure:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = self.directory / "resources.json"
        self.state = (
            json.loads(self.file.read_text())
            if self.file.exists()
            else {
                "region": REGION,
                "project": NAME,
                "deployment_id": uuid4().hex,
            }
        )
        self.session = boto3.Session(region_name=REGION)
        self.account = self.session.client("sts").get_caller_identity()["Account"]
        self.ec2 = self.session.client("ec2")
        self.iam = self.session.client("iam")
        self.cf = self.session.client("cloudfront")
        self.elb = self.session.client("elbv2")
        self.s3 = self.session.client("s3")
        self.ddb = self.session.client("dynamodb")
        self.ecs = self.session.client("ecs")
        self.ecr = self.session.client("ecr")

    def save(self):
        temporary = self.file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2, default=str) + "\n")
        temporary.chmod(0o600)
        temporary.replace(self.file)

    def ensure(self, key, create):
        if key not in self.state:
            self.state[key] = create()
            self.save()
        return self.state[key]

    def tag(self, resource, suffix):
        self.ec2.create_tags(
            Resources=[resource], Tags=TAGS + [{"Key": "Name", "Value": NAME + suffix}]
        )
        return resource

    def role(self, suffix, service, policy, managed=()):
        name = NAME + suffix
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": service},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
        try:
            role = self.iam.create_role(
                RoleName=name,
                AssumeRolePolicyDocument=json.dumps(trust),
                Tags=TAGS,
            )["Role"]
        except self.iam.exceptions.EntityAlreadyExistsException:
            role = self.iam.get_role(RoleName=name)["Role"]
            assert {t["Key"]: t["Value"] for t in role.get("Tags", [])}.get(
                "Project"
            ) == "DeciderModelRouter"
        if policy:
            self.iam.put_role_policy(
                RoleName=name,
                PolicyName="GatewayAccess",
                PolicyDocument=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": policy,
                    }
                ),
            )
        for arn in managed:
            self.iam.attach_role_policy(RoleName=name, PolicyArn=arn)
        return role["Arn"]

    def table(self, suffix, composite=False):
        name = NAME + suffix
        attrs = [{"AttributeName": "pk", "AttributeType": "S"}]
        keys = [{"AttributeName": "pk", "KeyType": "HASH"}]
        if composite:
            attrs.append({"AttributeName": "sk", "AttributeType": "S"})
            keys.append({"AttributeName": "sk", "KeyType": "RANGE"})
        try:
            self.ddb.create_table(
                TableName=name,
                AttributeDefinitions=attrs,
                KeySchema=keys,
                BillingMode="PAY_PER_REQUEST",
                SSESpecification={"Enabled": True},
                Tags=TAGS,
            )
        except self.ddb.exceptions.ResourceInUseException:
            pass
        self.ddb.get_waiter("table_exists").wait(TableName=name)
        if (
            self.ddb.describe_time_to_live(TableName=name)["TimeToLiveDescription"][
                "TimeToLiveStatus"
            ]
            == "DISABLED"
        ):
            self.ddb.update_time_to_live(
                TableName=name,
                TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
            )
        self.ddb.update_continuous_backups(
            TableName=name,
            PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
        )
        return name

    def bucket(self):
        name = f"{NAME}-{self.account}-{REGION}"
        try:
            self.s3.create_bucket(Bucket=name)
        except self.s3.exceptions.BucketAlreadyOwnedByYou:
            pass
        self.s3.put_public_access_block(
            Bucket=name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        self.s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Enabled"})
        self.s3.put_bucket_encryption(
            Bucket=name,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}],
            },
        )
        self.s3.put_bucket_tagging(Bucket=name, Tagging={"TagSet": TAGS})
        return name

    def network(self):
        print("Creating dedicated VPC, subnets and security groups...", flush=True)
        vpc = self.ensure(
            "vpc",
            lambda: self.tag(
                self.ec2.create_vpc(CidrBlock="10.146.0.0/16")["Vpc"]["VpcId"],
                "-vpc",
            ),
        )
        for attribute in ("EnableDnsSupport", "EnableDnsHostnames"):
            self.ec2.modify_vpc_attribute(VpcId=vpc, **{attribute: {"Value": True}})
        igw = self.ensure(
            "internet_gateway",
            lambda: self.tag(
                self.ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"],
                "-igw",
            ),
        )
        try:
            self.ec2.attach_internet_gateway(InternetGatewayId=igw, VpcId=vpc)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "Resource.AlreadyAssociated":
                raise
        azs = [
            z["ZoneName"]
            for z in self.ec2.describe_availability_zones(
                Filters=[{"Name": "state", "Values": ["available"]}],
            )["AvailabilityZones"]
        ][:2]
        for i, az in enumerate(azs):
            for kind, offset in (("public", 0), ("private", 10)):
                self.ensure(
                    f"{kind}_subnet_{i}",
                    lambda az=az, i=i, kind=kind, offset=offset: self.tag(
                        self.ec2.create_subnet(
                            VpcId=vpc, CidrBlock=f"10.146.{offset + i}.0/24", AvailabilityZone=az
                        )["Subnet"]["SubnetId"],
                        f"-{kind}-{i}",
                    ),
                )
        public_rt = self.ensure(
            "public_route_table",
            lambda: self.ec2.create_route_table(VpcId=vpc)["RouteTable"]["RouteTableId"],
        )
        self.ec2.create_route(
            RouteTableId=public_rt, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw
        )
        for i in range(2):
            self.ensure(
                f"public_association_{i}",
                lambda i=i: self.ec2.associate_route_table(
                    RouteTableId=public_rt,
                    SubnetId=self.state[f"public_subnet_{i}"],
                )["AssociationId"],
            )
        eip = self.ensure(
            "nat_eip", lambda: self.ec2.allocate_address(Domain="vpc")["AllocationId"]
        )

        def create_nat():
            for i in (0, 1):
                try:
                    result = self.ec2.create_nat_gateway(
                        SubnetId=self.state[f"public_subnet_{i}"],
                        AllocationId=eip,
                        ClientToken=self.state["deployment_id"] + f"-{i}",
                        TagSpecifications=[{"ResourceType": "natgateway", "Tags": TAGS}],
                    )["NatGateway"]
                    self.state["nat_subnet"] = self.state[f"public_subnet_{i}"]
                    return result["NatGatewayId"]
                except ClientError as exc:
                    if exc.response["Error"]["Code"] != "NatGatewayLimitExceeded" or i == 1:
                        raise
                    print(
                        "NAT quota exhausted in the first AZ; using the second dedicated subnet.",
                        flush=True,
                    )

        nat = self.ensure("nat_gateway", create_nat)
        while (
            self.ec2.describe_nat_gateways(NatGatewayIds=[nat])["NatGateways"][0]["State"]
            != "available"
        ):
            print("Waiting for the dedicated NAT gateway...", flush=True)
            time.sleep(10)
        private_rt = self.ensure(
            "private_route_table",
            lambda: self.ec2.create_route_table(VpcId=vpc)["RouteTable"]["RouteTableId"],
        )
        self.ec2.create_route(
            RouteTableId=private_rt, DestinationCidrBlock="0.0.0.0/0", NatGatewayId=nat
        )
        for i in range(2):
            self.ensure(
                f"private_association_{i}",
                lambda i=i: self.ec2.associate_route_table(
                    RouteTableId=private_rt,
                    SubnetId=self.state[f"private_subnet_{i}"],
                )["AssociationId"],
            )
        for name in ("alb", "api", "gpu"):
            self.ensure(
                name + "_sg",
                lambda name=name: self.ec2.create_security_group(
                    GroupName=NAME + "-" + name,
                    Description=NAME + " " + name,
                    VpcId=vpc,
                    TagSpecifications=[{"ResourceType": "security-group", "Tags": TAGS}],
                )["GroupId"],
            )
        self.ingress(self.state["api_sg"], 8000, source=self.state["alb_sg"])
        for port in (18124, 8100):
            self.ingress(self.state["gpu_sg"], port, source=self.state["api_sg"])
        prefix = self.ec2.describe_managed_prefix_lists(
            Filters=[
                {
                    "Name": "prefix-list-name",
                    "Values": ["com.amazonaws.global.cloudfront.origin-facing"],
                }
            ],
        )["PrefixLists"][0]["PrefixListId"]
        self.ingress(self.state["alb_sg"], 80, prefix=prefix)

    def ingress(self, group, port, *, source=None, prefix=None):
        permission = {"IpProtocol": "tcp", "FromPort": port, "ToPort": port}
        permission.update(
            {"UserIdGroupPairs": [{"GroupId": source}]}
            if source
            else {"PrefixListIds": [{"PrefixListId": prefix}]}
        )
        try:
            self.ec2.authorize_security_group_ingress(GroupId=group, IpPermissions=[permission])
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                raise

    def platform(self):
        self.ensure("bucket", self.bucket)
        self.ensure("auth_table", lambda: self.table("-auth"))
        self.ensure("data_table", lambda: self.table("-data", composite=True))
        self.ensure(
            "cluster",
            lambda: self.ecs.create_cluster(
                clusterName=NAME,
                tags=[{"key": t["Key"], "value": t["Value"]} for t in TAGS],
                settings=[{"name": "containerInsights", "value": "enabled"}],
            )["cluster"]["clusterName"],
        )
        self.ensure(
            "repository",
            lambda: self.ecr.create_repository(
                repositoryName=NAME,
                imageScanningConfiguration={"scanOnPush": True},
                imageTagMutability="IMMUTABLE",
                tags=[{"Key": t["Key"], "Value": t["Value"]} for t in TAGS],
            )["repository"]["repositoryUri"],
        )
        logs = self.session.client("logs")
        try:
            logs.create_log_group(
                logGroupName="/" + NAME + "/api", tags={"Project": "DeciderModelRouter"}
            )
        except logs.exceptions.ResourceAlreadyExistsException:
            pass
        logs.put_retention_policy(logGroupName="/" + NAME + "/api", retentionInDays=30)
        table_arns = [
            f"arn:aws:dynamodb:{REGION}:{self.account}:table/{self.state[k]}"
            for k in ("auth_table", "data_table")
        ]
        self.ensure(
            "task_role",
            lambda: self.role(
                "-task",
                "ecs-tasks.amazonaws.com",
                [
                    {
                        "Effect": "Allow",
                        "Action": [
                            "dynamodb:GetItem",
                            "dynamodb:PutItem",
                            "dynamodb:DeleteItem",
                            "dynamodb:UpdateItem",
                            "dynamodb:Query",
                            "dynamodb:TransactWriteItems",
                        ],
                        "Resource": table_arns,
                    },
                    {
                        "Effect": "Allow",
                        "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                        "Resource": [
                            "arn:aws:bedrock:*::foundation-model/*",
                            f"arn:aws:bedrock:*:{self.account}:inference-profile/*",
                        ],
                    },
                ],
            ),
        )
        self.ensure(
            "execution_role",
            lambda: self.role(
                "-execution",
                "ecs-tasks.amazonaws.com",
                [],
                [
                    "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
                ],
            ),
        )
        self.ensure(
            "gpu_role",
            lambda: self.role(
                "-gpu",
                "ec2.amazonaws.com",
                [],
                [
                    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
                ],
            ),
        )

        def profile():
            name = NAME + "-gpu"
            result = self.iam.create_instance_profile(InstanceProfileName=name, Tags=TAGS)
            self.iam.add_role_to_instance_profile(InstanceProfileName=name, RoleName=name)
            return result["InstanceProfile"]["Arn"]

        self.ensure("gpu_profile", profile)

    def edge(self):
        print("Creating independent ALB and CloudFront distribution...", flush=True)
        alb = self.ensure(
            "alb",
            lambda: self.elb.create_load_balancer(
                Name=NAME,
                Subnets=[self.state[f"private_subnet_{i}"] for i in range(2)],
                SecurityGroups=[self.state["alb_sg"]],
                Scheme="internal",
                Type="application",
                Tags=TAGS,
            )["LoadBalancers"][0],
        )
        tg = self.ensure(
            "target_group",
            lambda: self.elb.create_target_group(
                Name=NAME,
                Protocol="HTTP",
                Port=8000,
                VpcId=self.state["vpc"],
                TargetType="ip",
                HealthCheckPath="/api/ready",
                HealthCheckIntervalSeconds=15,
                HealthyThresholdCount=2,
                UnhealthyThresholdCount=3,
                Tags=TAGS,
            )["TargetGroups"][0]["TargetGroupArn"],
        )
        self.ensure(
            "listener",
            lambda: self.elb.create_listener(
                LoadBalancerArn=alb["LoadBalancerArn"],
                Protocol="HTTP",
                Port=80,
                DefaultActions=[{"Type": "forward", "TargetGroupArn": tg}],
            )["Listeners"][0]["ListenerArn"],
        )
        self.elb.modify_load_balancer_attributes(
            LoadBalancerArn=alb["LoadBalancerArn"],
            Attributes=[
                {"Key": "idle_timeout.timeout_seconds", "Value": "180"},
            ],
        )
        self.elb.get_waiter("load_balancer_available").wait(
            LoadBalancerArns=[alb["LoadBalancerArn"]]
        )
        origin = self.ensure(
            "vpc_origin",
            lambda: self.cf.create_vpc_origin(
                VpcOriginEndpointConfig={
                    "Name": NAME,
                    "Arn": alb["LoadBalancerArn"],
                    "HTTPPort": 80,
                    "HTTPSPort": 443,
                    "OriginProtocolPolicy": "http-only",
                    "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                },
            )["VpcOrigin"]["Id"],
        )
        while self.cf.get_vpc_origin(Id=origin)["VpcOrigin"]["Status"] != "Deployed":
            print("Waiting for the independent CloudFront VPC origin...", flush=True)
            time.sleep(15)
        groups = self.ec2.describe_security_groups(
            Filters=[
                {"Name": "vpc-id", "Values": [self.state["vpc"]]},
                {"Name": "group-name", "Values": ["CloudFront-VPCOrigins-Service-SG"]},
            ]
        )["SecurityGroups"]
        for group in groups:
            self.ingress(self.state["alb_sg"], 80, source=group["GroupId"])
        if not groups:
            raise RuntimeError("CloudFront VPC origin service security group is missing")
        oac = self.ensure(
            "oac",
            lambda: self.cf.create_origin_access_control(
                OriginAccessControlConfig={
                    "Name": NAME,
                    "OriginAccessControlOriginType": "s3",
                    "SigningBehavior": "always",
                    "SigningProtocol": "sigv4",
                },
            )["OriginAccessControl"]["Id"],
        )
        cache = self.ensure(
            "static_cache_policy",
            lambda: self.cf.create_cache_policy(
                CachePolicyConfig={
                    "Name": NAME,
                    "MinTTL": 0,
                    "DefaultTTL": 60,
                    "MaxTTL": 300,
                    "ParametersInCacheKeyAndForwardedToOrigin": {
                        "EnableAcceptEncodingGzip": True,
                        "EnableAcceptEncodingBrotli": True,
                        "HeadersConfig": {"HeaderBehavior": "none"},
                        "CookiesConfig": {"CookieBehavior": "none"},
                        "QueryStringsConfig": {"QueryStringBehavior": "none"},
                    },
                },
            )["CachePolicy"]["Id"],
        )

        def distribution():
            methods = {
                "Quantity": 2,
                "Items": ["GET", "HEAD"],
                "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
            }
            result = self.cf.create_distribution(
                DistributionConfig={
                    "CallerReference": self.state["deployment_id"],
                    "Comment": "Independent DeciderModelRouter",
                    "Enabled": True,
                    "DefaultRootObject": "index.html",
                    "PriceClass": "PriceClass_100",
                    "HttpVersion": "http2and3",
                    "IsIPV6Enabled": True,
                    "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
                    "Origins": {
                        "Quantity": 2,
                        "Items": [
                            {
                                "Id": "frontend",
                                "DomainName": f"{self.state['bucket']}.s3.{REGION}.amazonaws.com",
                                "OriginAccessControlId": oac,
                                "S3OriginConfig": {"OriginAccessIdentity": ""},
                            },
                            {
                                "Id": "api",
                                "DomainName": alb["DNSName"],
                                "VpcOriginConfig": {
                                    "VpcOriginId": origin,
                                    "OriginReadTimeout": 60,
                                    "OriginKeepaliveTimeout": 5,
                                },
                            },
                        ],
                    },
                    "DefaultCacheBehavior": {
                        "TargetOriginId": "frontend",
                        "ViewerProtocolPolicy": "redirect-to-https",
                        "AllowedMethods": methods,
                        "CachePolicyId": cache,
                        "Compress": True,
                    },
                    "CacheBehaviors": {
                        "Quantity": 1,
                        "Items": [
                            {
                                "PathPattern": "/api/*",
                                "TargetOriginId": "api",
                                "ViewerProtocolPolicy": "https-only",
                                "AllowedMethods": {
                                    "Quantity": 7,
                                    "Items": [
                                        "GET",
                                        "HEAD",
                                        "OPTIONS",
                                        "PUT",
                                        "PATCH",
                                        "POST",
                                        "DELETE",
                                    ],
                                    "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                                },
                                "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
                                "OriginRequestPolicyId": "b689b0a8-53d0-40ab-baf2-68738e2966ac",
                                "Compress": False,
                            }
                        ],
                    },
                }
            )["Distribution"]
            return {k: result[k] for k in ("Id", "ARN", "DomainName")}

        dist = self.ensure("distribution", distribution)
        self.s3.put_bucket_policy(
            Bucket=self.state["bucket"],
            Policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "cloudfront.amazonaws.com"},
                            "Action": "s3:GetObject",
                            "Resource": f"arn:aws:s3:::{self.state['bucket']}/*",
                            "Condition": {"StringEquals": {"AWS:SourceArn": dist["ARN"]}},
                        }
                    ],
                }
            ),
        )
        self.s3.put_object(
            Bucket=self.state["bucket"],
            Key="index.html",
            ContentType="text/html; charset=utf-8",
            CacheControl="no-cache, max-age=0, must-revalidate",
            Body=b'<!doctype html><html><head><meta http-equiv="refresh" content="0;url=/gateway/index.html"><title>DeciderModelRouter</title></head><body><a href="/gateway/index.html">Open DeciderModelRouter</a></body></html>',
        )
        self.state["url"] = "https://" + dist["DomainName"]
        self.save()

    def gpu_image(self, source):
        self.ensure(
            "gpu_image",
            lambda: self.ec2.create_image(
                InstanceId=source,
                Name=NAME + "-" + self.state["deployment_id"][:12],
                NoReboot=True,
                Description="Private copy of validated model runtimes; independent EC2 deployment.",
                TagSpecifications=[
                    {"ResourceType": "image", "Tags": TAGS},
                    {"ResourceType": "snapshot", "Tags": TAGS},
                ],
            )["ImageId"],
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--source-gpu", help="Create a private, no-reboot runtime image from this GPU"
    )
    args = parser.parse_args()
    infra = Infrastructure(args.output)
    if args.source_gpu:
        infra.gpu_image(args.source_gpu)
    infra.network()
    infra.platform()
    infra.edge()
    print(
        json.dumps(
            {
                "phase": "infrastructure_ready",
                "url": infra.state["url"],
                "resources": str(infra.file),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
