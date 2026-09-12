import json
import sys
from pathlib import Path

run=Path(__file__).resolve().parent
sys.path.insert(0,str(run.parents[1]/'scripts'))
from pil_capabilities import invoke

source=run/'proof-v2'
out=run/'refined-proof'
out.mkdir(exist_ok=True)
rows=[]


def call(label,tool,argv):
    result=invoke(tool,list(map(str,argv)))
    (out/(label+'.json')).write_text(json.dumps(result,indent=2,sort_keys=True),encoding='utf-8')
    assert result['ok'],result
    rows.append({'tool':tool,'label':label,'ok':True})
    return result['result']


registered=call('registration','pil_register',[source/'texture.png',source/'shift.png','--output-dir',out/'aligned'])
assert registered['aligned']['delta_e_mean']<.05
call('registered-original-analyzer','pil_image_analyze',[source/'texture.png',out/'aligned/aligned.png'])
for domain in ('screenshot','photograph','transparent-render','concept-versus-render'):
    profile=out/(domain+'-profile.json')
    call(domain+'-build','pil_calibrate',['build',source/(domain+'-corpus.json'),'--output',profile])
    pair=json.loads((source/(domain+'-corpus.json')).read_text())['pairs'][-1]
    result=call(domain+'-evaluate','pil_calibrate',['evaluate',profile,pair['a'],pair['b'],'--domain',domain])
    assert result['verdict']=='CHANGE_DETECTED'
(out/'matrix.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
print('Refined registration mean Delta E:',registered['raw']['delta_e_mean'],'->',registered['aligned']['delta_e_mean'])
print('Successful refinement invocations:',len(rows))
