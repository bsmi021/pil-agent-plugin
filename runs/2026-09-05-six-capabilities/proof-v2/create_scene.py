import bpy
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add()
bpy.context.object.name='Body'
mesh=bpy.data.meshes.new('GarmentMesh')
mesh.from_pydata([(-.8,-.8,1.005),(.8,-.8,1.005),(.8,.8,1.005),(-.8,.8,1.005)],[],[(0,1,2,3)])
mesh.update()
obj=bpy.data.objects.new('Garment',mesh)
bpy.context.collection.objects.link(obj)
bpy.ops.wm.save_as_mainfile(filepath='C:\\Projects\\pil-agent-plugin\\pil-agent-plugin\\runs\\2026-09-05-six-capabilities\\proof-v2\\scene.blend')
