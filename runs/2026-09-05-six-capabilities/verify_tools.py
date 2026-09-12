"""Persistent execution/quality evidence for every public CLI and MCP transport."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from pil_capabilities import invoke,names
from pil_blender_mesh import resolve_blender_executable

RUN=BASE/os.environ.get('PIL_PROOF_DIRECTORY','proof-v2')
RUN.mkdir(exist_ok=True)
receipts=[]


def js(name,value):
    path=RUN/name
    path.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False),encoding='utf-8')
    return path


def call(tool,*argv,label=None,check=None):
    label=label or tool
    start=time.monotonic()
    result=invoke(tool,list(map(str,argv)),timeout=500)
    js(label+'.json',result)
    row={'tool':tool,'label':label,'ok':result['ok'],'exit_code':result['exit_code'],'seconds':round(time.monotonic()-start,2),'receipt':label+'.json'}
    receipts.append(row);js('matrix.json',receipts)
    print(label, 'PASS' if result['ok'] else result['error'],flush=True)
    assert result['ok'],result
    if check:check(result['result'])
    return result['result']


def require(condition,message):
    assert condition,message


def main():
    rng=np.random.default_rng(412)
    a=Image.fromarray(rng.integers(30,210,(128,160,3),dtype='uint8'))
    a.save(RUN/'texture.png')
    shift=Image.new('RGB',a.size);shift.paste(a,(5,-3));shift.save(RUN/'shift.png')
    gray=Image.new('RGB',(600,600),'gray');gray.save(RUN/'base.png')
    d=ImageDraw.Draw(gray);d.rectangle((10,10,12,12),fill='red');d.rectangle((500,500,503,503),fill='blue');gray.save(RUN/'changes.png')
    shape=Image.new('RGBA',(160,160));ImageDraw.Draw(shape).polygon([(30,130),(80,20),(130,130)],fill=(60,140,220,200));shape.save(RUN/'shape.png')
    text=Image.new('RGB',(800,140),'white')
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',48)
    ImageDraw.Draw(text).text((30,35),'PIL QUALITY 123',font=font,fill='black');text.save(RUN/'text.png')
    exif=Image.Exif();exif[274]=6;a.save(RUN/'oriented.png',exif=exif)
    ImageOps.exif_transpose(Image.open(RUN/'oriented.png')).save(RUN/'display.png')
    # Exercise all original 2D tools, including diagnostic/demoted tools without promoting their claims.
    for tool in ('pil_image_info','pil_image_analyze','pil_palette_diff','pil_structure_diff','pil_components','pil_silhouette'):
        extra=[RUN/'shape.png'] if tool in ('pil_image_analyze','pil_palette_diff','pil_structure_diff') else []
        call(tool,RUN/'shape.png',*extra)
    call('pil_alignment','alignment',RUN/'texture.png',RUN/'shift.png',label='alignment-diagnostic')
    call('pil_alignment','contrast','--colors','#ffffff','#000000',label='contrast-standard',
         check=lambda r:require('21' in json.dumps(r),'expected black-white contrast 21'))
    call('pil_crop',RUN/'changes.png','--region','0,0,0.1,0.1','--out',RUN/'crop.png','--scale','4')
    call('pil_annotate',RUN/'changes.png','--box','0,0,0.1,0.1','--label','local edit','--out',RUN/'annotated.png')
    contract=js('contract.json',{'invariant':['layout.preserved','palette.preserved']})
    call('pil_contract_verdict',RUN/'shape.png',RUN/'shape.png','--contract',contract)
    call('pil_ocr',RUN/'text.png','--psm','7','--claims-out',RUN/'ocr-claims.json',
         check=lambda r:require('PIL QUALITY 123' in r['full_text'],'OCR text did not match fixture'))
    sealed=call('pil_semantic_record','seal',RUN/'text.png','--claims',RUN/'ocr-claims.json')
    record=js('sealed-record.json',sealed)
    call('pil_semantic_record','verify',RUN/'text.png','--record',record,label='semantic-verify')
    call('pil_semantic_record','compare','--record-a',record,'--record-b',record,label='semantic-compare')
    model=BASE/'mobilenetv2-12.onnx'
    embedded=call('pil_embed','embed',RUN/'texture.png',RUN/'texture.png','--model',model,'--preprocessing','imagenet',
                  check=lambda r:require(abs(r['diff']['cosine_similarity']-1)<1e-6,'embedding identity failed'))
    fp=js('fingerprint.json',embedded)
    call('pil_embed','compare','--fingerprint-a',fp,'--fingerprint-b',fp,label='embed-compare')
    call('pil_bootstrap','install','--ocr','--embedding','--reconstruction','--comparison','--mcp','--model',model,'--preprocessing','imagenet')
    # New input normalization is measured by the original image analyzer.
    call('pil_normalize',RUN/'oriented.png','--output',RUN/'normalized.png')
    normalized=call('pil_image_analyze',RUN/'normalized.png',RUN/'display.png',label='normalization-quality',
                    check=lambda r:require(r['diff']['structure']['changed_area_fraction']==0,'normalized pixels differ'))
    spec=js('mask-spec.json',{'name':'subject','polygons':[[[25,15],[135,15],[135,135],[25,135]]],
                              'subtract_polygons':[[[65,70],[95,70],[95,90],[65,90]]]})
    call('pil_mask',RUN/'shape.png','--spec',spec,'--output',RUN/'mask.json')
    for tool in ('pil_palette_diff','pil_structure_diff','pil_image_analyze','pil_embed'):
        options=['--','--model',model,'--preprocessing','imagenet'] if tool=='pil_embed' else []
        call('pil_pipeline','--tool',tool,'--image-a',RUN/'shape.png','--mask-a',RUN/'mask.json',*options,label='pipeline-'+tool)
    textspec=js('text-mask-spec.json',{'name':'text','polygons':[[[10,10],[790,10],[790,130],[10,130]]]})
    call('pil_mask',RUN/'text.png','--spec',textspec,'--output',RUN/'text-mask.json',label='text-mask-command')
    call('pil_pipeline','--tool','pil_ocr','--image-a',RUN/'text.png','--mask-a',RUN/'text-mask.json','--','--psm','7',label='pipeline-ocr',
         check=lambda r:require('PIL QUALITY 123' in r['result']['full_text'],'masked OCR differs'))
    reg=call('pil_register',RUN/'texture.png',RUN/'shift.png','--output-dir',RUN/'registered',
             check=lambda r:require(r['status']=='REGISTERED' and r['aligned']['delta_e_mean']<r['raw']['delta_e_mean']/10,'registration did not improve'))
    call('pil_structure_diff',RUN/'texture.png',RUN/'registered/aligned.png',label='registered-original-structure')
    dif=call('pil_diff_regions',RUN/'base.png',RUN/'changes.png','--ssim','--output-dir',RUN/'local-diff',
             check=lambda r:require(r['changed_pixels']==25 and len(r['regions'])==2,'local diff did not recover both edits'))
    call('pil_image_analyze',RUN/'base.png',RUN/'changes.png',label='tiny-edits-original-analyzer')
    # Four named domain profile examples; synthetic smoke corpora, not production calibration claims.
    for domain in ('screenshot','photograph','transparent-render','concept-versus-render'):
        pairs=[]
        for split in ('train','validation'):
            for i in range(4):
                seed=100+i+(20 if split=='validation' else 0)
                arr=np.random.default_rng(seed).integers(50,180,(64,64,3),dtype='uint8')
                image=Image.fromarray(arr)
                if domain=='screenshot':
                    image=Image.new('RGB',(64,64),(seed,70,100));ImageDraw.Draw(image).rectangle((5,5,50,30),fill=(160,seed,180))
                if domain=='transparent-render':
                    image=image.convert('RGBA');alpha=Image.new('L',(64,64));ImageDraw.Draw(alpha).ellipse((8,8,56,56),fill=200);image.putalpha(alpha)
                source=RUN/f'{domain}-{split}-{i}.png';image.save(source)
                candidate=RUN/f'{domain}-{split}-{i}-change.png';image.paste('red',(20,20,28,28));image.save(candidate)
                for changed,b in [(False,source),(True,candidate)]:
                    pairs.append({'source':f'{split}-{i}','split':split,'a':str(source),'b':str(b),'changed':changed,'perturbation':'8px_patch','magnitude':64/4096})
        manifest=js(domain+'-corpus.json',{'schema':'calibration-corpus-v1','domain':domain,'pairs':pairs,
                                            'scope':'synthetic execution smoke only; no production-domain accuracy claim'})
        profile=RUN/(domain+'-profile.json')
        call('pil_calibrate','build',manifest,'--output',profile,label=domain+'-calibration',
             check=lambda r:require(r['validation']['accepted'],'held-out smoke failed'))
        call('pil_calibrate','evaluate',profile,pairs[-1]['a'],pairs[-1]['b'],'--domain',domain,label=domain+'-profile-use',
             check=lambda r:require(r['verdict']=='CHANGE_DETECTED','profile did not detect change'))
    call('pil_capabilities','--output',RUN/'catalog.json')
    jobs=js('batch.json',[{'id':'good','tool':'pil_image_info','argv':[str(RUN/'shape.png')]},{'id':'bad','tool':'pil_mask','argv':[]}])
    batch_result=invoke('pil_capabilities',['--batch',str(jobs),'--output',str(RUN/'batch-results.json'),'--summary'])
    js('batch-partial.json',batch_result)
    require(batch_result['exit_code']==1 and len(batch_result['result']['items'])==2,'batch lost failure')
    # Real Blender geometry, fit, matched renders and reconstruction pipeline.
    blender=resolve_blender_executable(None);require(blender is not None,'Blender missing')
    scene=RUN/'scene.blend'
    creator=RUN/'create_scene.py'
    creator.write_text("import bpy\nbpy.ops.object.select_all(action='SELECT')\nbpy.ops.object.delete(use_global=False)\nbpy.ops.mesh.primitive_cube_add()\nbpy.context.object.name='Body'\nmesh=bpy.data.meshes.new('GarmentMesh')\nmesh.from_pydata([(-.8,-.8,1.005),(.8,-.8,1.005),(.8,.8,1.005),(-.8,.8,1.005)],[],[(0,1,2,3)])\nmesh.update()\nobj=bpy.data.objects.new('Garment',mesh)\nbpy.context.collection.objects.link(obj)\nbpy.ops.wm.save_as_mainfile(filepath="+repr(str(scene))+")\n")
    proc=subprocess.run([blender,'--factory-startup','--background','--python',str(creator),'--python-exit-code','1'],capture_output=True,text=True,stdin=subprocess.DEVNULL,timeout=180)
    (RUN/'scene-creation.txt').write_text(proc.stdout+proc.stderr);require(proc.returncode==0,'scene creation failed')
    call('pil_blender_mesh',scene)
    call('pil_blender_fit',scene,'--body-object','Body','--garment-object','Garment','--clearance','.02','--mode','probe',label='clearance-before')
    fitted=RUN/'fitted.blend'
    call('pil_blender_fit',scene,'--body-object','Body','--garment-object','Garment','--clearance','.02','--max-displacement','.05','--mode','apply-copy','--output',fitted,
         check=lambda r:require(r['fit']['after']['minimum_signed_clearance']>=.0199,'fitting did not meet clearance'))
    call('pil_blender_render',fitted,'--view','front','--resolution','128','--out',RUN/'front.png')
    call('pil_character_sheet_review',fitted,'--contract',contract,'--view','front:'+str(RUN/'front.png'))
    directions=[('front',[0,-1,0]),('front_right',[1,-1,0]),('right',[1,0,0]),('back_right',[1,1,0]),('back',[0,1,0]),('back_left',[-1,1,0]),('front_left',[-1,-1,0])]
    render_spec=js('render-spec.json',{'schema':'render-views-v1','views':[{'name':n,'direction':v} for n,v in directions]})
    rendered=call('pil_multiview_render',fitted,'--manifest',render_spec,'--output-dir',RUN/'views','--width','128','--height','128',
                  check=lambda r:require(len(r['render']['views'])==7,'missing named views'))
    views=rendered['render']['views']
    multiview=js('multiview.json',{'schema':'multiview-spec-v1','views':[{'name':v['name'],'image':v['path']} for v in views]})
    call('pil_multiview_prepare',multiview)
    review=js('review.json',{'schema':'review-views-v1','views':[{'name':v['name'],'reference':v['path'],'render':v['path']} for v in views]})
    call('pil_multiview_review','--manifest',review,'--contract',contract)
    truth=np.array([[-1,.2,0],[1,.2,0],[0,.8,2]])
    template=js('template.json',{'schema':'template-mesh-v1','vertices':(truth+[.1,-.1,.1]).tolist(),'faces':[[0,1,2]]})
    correspondences=[]
    for name,matrix in [('front',[[1,0,0],[0,0,1]]),('right',[[0,1,0],[0,0,1]])]:
        correspondences.append({'name':name,'projection_matrix':matrix,'landmarks':[{'vertex':i,'target':v.tolist()} for i,v in enumerate(truth@np.array(matrix).T)]})
    correspondence_file=js('correspondences.json',{'schema':'correspondences-v1','views':correspondences})
    constraints=js('constraints.json',{'schema':'geometry-constraints-v1','template_weight':.0001})
    call('pil_multiview_solve','--template',template,'--correspondences',correspondence_file,'--constraints',constraints,'--output',RUN/'solution.json',
         check=lambda r:require(np.allclose(r['vertices'],truth,atol=.001),'known 3D landmarks not recovered'))
    job=js('job.json',{'schema':'reconstruction-job-v1','spec':str(multiview),'template':str(template),'correspondences':str(correspondence_file),'constraints':str(constraints)})
    call('pil_reconstruct',job,'--output-dir',RUN/'reconstruction',check=lambda r:require(r['status']=='COMPLETED','reconstruction not completed'))
    missing=set(names())-{r['tool'] for r in receipts}
    require(not missing,f'public tools missing proof: {missing}')
    # Real protocol receipt in addition to the automated test.
    async def protocol():
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(ROOT/'scripts/pil_mcp.py')])) as (read,write):
            async with ClientSession(read,write) as session:
                initialized=await session.initialize();listed=await session.list_tools()
                result=await session.call_tool('pil_image_info',{'argv':[str(RUN/'shape.png')]})
                require(not result.isError,'MCP call failed')
                js('mcp-protocol.json',{'server':initialized.serverInfo.model_dump(),'tool_count':len(listed.tools),'call':result.model_dump(mode='json')})
    asyncio.run(protocol())
    js('summary.json',{'public_cli_tools':len(names()),'successful_invocations':len(receipts),'missing_tools':sorted(missing),
                       'registration':reg,'local_difference':dif,'mcp':'successful real stdio handshake/list/call',
                       'calibration_scope':'four synthetic smoke corpora, source-disjoint; production transfer not claimed'})
    print('ALL PUBLIC TOOLS VERIFIED',len(names()),flush=True)


if __name__=='__main__':
    main()
