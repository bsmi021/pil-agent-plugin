import json
import os
import subprocess
import sys
import time
from pathlib import Path

run=Path(__file__).resolve().parent
root=run.parents[1]
env=os.environ.copy()
env['PIL_AGENT_EMBED_MODEL']=str(run/'mobilenetv2-12.onnx')
env['PIL_AGENT_EMBED_PREPROCESSING']='imagenet'
sys.path.insert(0,str(root/'scripts'))
from pil_ocr import _find_tesseract
tesseract=_find_tesseract(None)
if tesseract:
    env['PATH']=str(Path(tesseract).parent)+os.pathsep+env.get('PATH','')
# Historical checks require the original private image and its measured values.
# Never substitute the generated shape fixture for that unavailable reference.
env.pop('PIL_AGENT_REFERENCE_IMAGE',None)
start=time.monotonic()
with (run/'full-suite-validated.txt').open('w',encoding='utf-8') as log:
    result=subprocess.run([sys.executable,'-m','pytest','-q'],cwd=root,env=env,
                          stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,timeout=900)
(run/'suite-validated-receipt.json').write_text(json.dumps({'exit_code':result.returncode,'seconds':time.monotonic()-start,
    'command':[sys.executable,'-m','pytest','-q'],'model':env['PIL_AGENT_EMBED_MODEL'],
    'tesseract':tesseract,'historical_reference':'unavailable; default skip behavior retained'},indent=2))
print((run/'full-suite-validated.txt').read_text(encoding='utf-8')[-7000:])
raise SystemExit(result.returncode)
