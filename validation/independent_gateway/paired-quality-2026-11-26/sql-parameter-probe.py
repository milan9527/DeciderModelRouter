import json,pathlib,subprocess,time,uuid
r=pathlib.Path('.deployment/paired-quality-20261126');name='dmr-paired-sql-'+uuid.uuid4().hex[:10]
image='pgvector/pgvector:pg15'
queries={
 'original_parameter_typing': "PREPARE probe AS SELECT jsonb_build_object('charge_id', $1::text, 'tenant_id', $2, 'event_id', $3, 'amount', $4::text, 'currency', $5);",
 'typed_positive_control': "PREPARE probe AS SELECT jsonb_build_object('charge_id', $1::text, 'tenant_id', $2::text, 'event_id', $3::text, 'amount', $4::text, 'currency', $5::text); EXECUTE probe('synthetic-charge','T2','EV9','199.0000','CNY');"
}
report={'scope':'Isolated reduced SQL type-inference probe only, not full industry code execution','image':image,'queries':{}}
try:
 subprocess.run(['docker','run','-d','--name',name,'--network','none','--read-only','--user','postgres','--cap-drop=ALL','--security-opt','no-new-privileges','--memory','256m','--cpus','1','--pids-limit','64','--tmpfs','/tmp:rw,nosuid,nodev,size=128m,mode=1777','--entrypoint','sh',image,'-c',"initdb -D /tmp/pg -A trust > /tmp/init.log && exec postgres -D /tmp/pg -k /tmp -c listen_addresses=''"],check=True,capture_output=True,text=True)
 for _ in range(20):
  q=subprocess.run(['docker','exec',name,'pg_isready','-h','/tmp'],capture_output=True,text=True)
  if q.returncode==0:break
  time.sleep(.5)
 else:raise RuntimeError('Temporary PostgreSQL failed to start')
 report['image_id']=subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True).strip()
 for label,query in queries.items():
  q=subprocess.run(['docker','exec','-i',name,'psql','-h','/tmp','-U','postgres','-v','ON_ERROR_STOP=1','-c',query],capture_output=True,text=True,timeout=15)
  report['queries'][label]={'sql':query,'returncode':q.returncode,'stdout':q.stdout,'stderr':q.stderr}
 assert report['queries']['original_parameter_typing']['returncode']!=0
 assert 'could not determine data type of parameter $2' in report['queries']['original_parameter_typing']['stderr']
 assert report['queries']['typed_positive_control']['returncode']==0
 report['confirmed']=True
finally:
 q=subprocess.run(['docker','rm','-f',name],capture_output=True,text=True)
 report['temporary_container_removed']=q.returncode==0
 (r/'sql-parameter-probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
