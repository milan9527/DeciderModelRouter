"""Add a private media bucket and scoped task permissions using AWS APIs."""

import argparse
import json
from pathlib import Path

import boto3

from deployment.independent_gateway import NAME, REGION, TAGS


def provision(path):
    resources = json.loads(path.read_text())
    assert resources["project"] == NAME and resources["cluster"] == NAME
    session = boto3.Session(region_name=REGION)
    account = session.client("sts").get_caller_identity()["Account"]
    bucket = f"{NAME}-media-{account}-{REGION}"
    s3, iam = session.client("s3"), session.client("iam")
    try:
        s3.create_bucket(Bucket=bucket)
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass
    s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": TAGS})
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        key: True for key in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")
    })
    s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={
        "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}],
    })
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
    s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={"Rules": [{
        "ID": "private-media-retention", "Status": "Enabled", "Filter": {"Prefix": "media/"},
        "Expiration": {"Days": 180}, "NoncurrentVersionExpiration": {"NoncurrentDays": 7},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
    }]})
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({
        "Version": "2012-10-17", "Statement": [{
            "Effect": "Deny", "Principal": "*", "Action": "s3:*",
            "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
            "Condition": {"Bool": {"aws:SecureTransport": "false"}},
        }],
    }))
    role = resources["task_role"].split("/")[-1]
    current = iam.get_role(RoleName=role)["Role"]
    assert {t["Key"]: t["Value"] for t in current.get("Tags", [])}.get("Project") == "DeciderModelRouter"
    iam.put_role_policy(RoleName=role, PolicyName="PrivateMediaAccess", PolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
             "Resource": f"arn:aws:s3:::{bucket}/media/*"},
            {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{bucket}",
             "Condition": {"StringLike": {"s3:prefix": ["media/*"]}}},
            {"Effect": "Allow", "Action": "bedrock:InvokeModelWithBidirectionalStream",
             "Resource": "arn:aws:bedrock:*::foundation-model/amazon.nova-*"},
            {"Effect": "Allow", "Action": "bedrock:GetAsyncInvoke",
             "Resource": f"arn:aws:bedrock:{REGION}:{account}:async-invoke/*"},
        ],
    }))
    resources["media_bucket"] = bucket
    path.write_text(json.dumps(resources, indent=2) + "\n")
    return {"media_bucket": bucket, "task_role": role, "retention_days": 180, "public_access": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(provision(args.resources), indent=2))


if __name__ == "__main__":
    main()
