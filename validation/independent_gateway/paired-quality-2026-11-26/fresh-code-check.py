import ast,hashlib,re
from pathlib import Path
from scripts.check_paired_code import isolated,CHECKER
from scripts.run_profile_quality import read,save
root=Path('.deployment/paired-quality-20261126')
image='sha256:4a8ae7021955547b827b5be2a1e9d7cdc15478cf21990f5edcd3791db41665f9'
for arm in ['joint','two_stage','cpu_policy']:
 row=read(root/arm/'additional/fresh-interval.json.gz');results=[]
 for index,source in enumerate(re.findall(r'```python\s*\n(.*?)```',row['output']['text'],re.S)):
  try:tree=ast.parse(source)
  except SyntaxError:continue
  for node in tree.body:
   if isinstance(node,ast.FunctionDef) and node.name.startswith('merge'):
    result=isolated(source+'\nmerge = '+node.name+'\n','merge',CHECKER,image)
    results.append({'block':index,'function':node.name,'original_block_sha256':hashlib.sha256(source.encode()).hexdigest(),'adaptation':'Bind original function to checker name merge; function body unchanged',**result})
 record={'method':'isolated_functional_tests_all_variants','variants':results,'status':'pass' if results and all(r['status']=='pass' for r in results) else 'fail','scope':'Function behavior only; explanations receive a separate review'}
 save(root/arm/'fresh-code-results.json',record)
 print(arm,record['status'],[(r['result'].get('passed'),r['result'].get('tests')) for r in results],flush=True)
