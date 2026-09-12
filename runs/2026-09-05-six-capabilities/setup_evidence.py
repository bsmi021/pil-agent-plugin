import hashlib
import json
import urllib.request
from pathlib import Path

root=Path(__file__).parent
target=root/'mobilenetv2-12.onnx'
expected='c0c3f76d93fa3fd6580652a45618618a220fced18babf65774ed169de0432ad5'
url='https://media.githubusercontent.com/media/onnx/models/main/validated/vision/classification/mobilenet/model/mobilenetv2-12.onnx'
if not target.exists():
    urllib.request.urlretrieve(url,target)
actual=hashlib.sha256(target.read_bytes()).hexdigest()
assert actual==expected,(actual,expected)
(root/'model-receipt.json').write_text(json.dumps({'url':url,'sha256':actual,'path':str(target),'purpose':'existing supported MobileNet inference verification'},indent=2))
print('Verified model',actual)
