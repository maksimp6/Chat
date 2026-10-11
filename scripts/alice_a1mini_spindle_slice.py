"""Slice hash-pinned OEM spindle source plate for A1 mini PLA fit prototype only."""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

from alice_a1mini_pla_cube_slice import profiles, _read_sliced_archive, _validate_profile, _validate_gcode

SOURCE_SHA = '21fe9c783b15d00421dfb39dd0c79666ac1fe6f8bb672eaec1c0e86decdbbf84'
OUTPUT = 'Alice_OEM_Spindle_v03_A1mini_PLA_FIT_PROTOTYPE.gcode.3mf'

def make_stl(src: Path, out: Path):
    raw = src.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA or len(raw) != 106016:
        raise ValueError('spindle source hash/size mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.testzip() is not None or set(z.namelist()) != {'3D/3dmodel.model','_rels/.rels','[Content_Types].xml'}:
            raise ValueError('source 3MF structure/CRC invalid')
        root = ET.fromstring(z.read('3D/3dmodel.model'))
    if root.get('unit') != 'millimeter': raise ValueError('source units')
    objects = root.findall('.//{*}resources/{*}object')
    items = root.findall('.//{*}build/{*}item')
    if len(objects) != 7 or len(items) != 7: raise ValueError('expected seven parts')
    meshes = {}
    for obj in objects:
        mesh = obj.find('{*}mesh')
        if mesh is None: raise ValueError('missing mesh')
        verts = [tuple(float(v.get(a)) for a in ('x','y','z')) for v in mesh.findall('{*}vertices/{*}vertex')]
        faces = [tuple(int(t.get(a)) for a in ('v1','v2','v3')) for t in mesh.findall('{*}triangles/{*}triangle')]
        if not verts or not faces or not all(math.isfinite(c) for v in verts for c in v): raise ValueError('invalid mesh')
        meshes[obj.get('id')] = (verts, faces)
    if sorted(len(v[1]) for v in meshes.values()) != [668,986,986,986,986,2524,2524]:
        raise ValueError('unexpected geometry')
    blob = bytearray(b'Alice OEM spindle v03 PLA fit prototype'.ljust(80,b' '))
    blob.extend(struct.pack('<I',9660))
    xs,ys,zs = [],[],[]
    for item in items:
        vertices,faces = meshes[item.get('objectid')]
        m = [float(n) for n in item.get('transform','').split()]
        if len(m) != 12 or not all(math.isfinite(n) for n in m): raise ValueError('invalid transform')
        def move(p):
            return (p[0]*m[0]+p[1]*m[3]+p[2]*m[6]+m[9],p[0]*m[1]+p[1]*m[4]+p[2]*m[7]+m[10],p[0]*m[2]+p[1]*m[5]+p[2]*m[8]+m[11])
        for face in faces:
            try: a,b,c = [move(vertices[n]) for n in face]
            except IndexError as e: raise ValueError('bad face index') from e
            u = [b[i]-a[i] for i in range(3)];v=[c[i]-a[i] for i in range(3)]
            normal=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
            length=math.sqrt(sum(q*q for q in normal))
            if length < 1e-9: raise ValueError('degenerate triangle')
            blob.extend(struct.pack('<12fH',*(tuple(q/length for q in normal)+a+b+c),0))
            xs.extend((a[0],b[0],c[0]));ys.extend((a[1],b[1],c[1]));zs.extend((a[2],b[2],c[2]))
    if len(blob)!=84+9660*50: raise ValueError('face count mismatch')
    for coords in (xs,ys,zs):
        if min(coords)<-0.01 or max(coords)>180: raise ValueError('outside A1 mini bed')
    out.write_bytes(blob)
    print('SOURCE_STL_VERIFIED',len(blob),hashlib.sha256(blob).hexdigest(),flush=True)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--source-3mf',type=Path,required=True)
    ap.add_argument('--slicer-root',type=Path,required=True)
    ap.add_argument('--output-dir',type=Path,required=True)
    args=ap.parse_args();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    root=args.slicer_root.resolve();binary=root/'bin/bambu-studio'
    if not binary.is_file(): raise FileNotFoundError(binary)
    stl=out/'spindle_plate.stl';make_stl(args.source_3mf,stl)
    machine,process,filament=profiles(root/'resources/profiles/BBL',out)
    p=json.loads(process.read_text());p.update(wall_loops='4',sparse_infill_density='35%')
    process.write_text(json.dumps(p,indent=2)+'\n')
    env=dict(os.environ,LC_ALL='C',LD_LIBRARY_PATH=str(root/'bin'))
    command=[str(binary),'--slice','0','--export-3mf',OUTPUT,'--load-settings',f'{machine};{process}','--load-filaments',str(filament),'--outputdir',str(out),str(stl)]
    result=subprocess.run(command,cwd=out,env=env,text=True,capture_output=True,timeout=240)
    print('SLICER_EXIT',result.returncode,'LOG_TAIL',(result.stdout+result.stderr)[-2500:],flush=True)
    if result.returncode: raise RuntimeError('Bambu Studio slicing failed')
    dest=out/OUTPUT
    settings,gcode=_read_sliced_archive(dest);_validate_profile(settings);_validate_gcode(gcode)
    if settings.get('wall_loops')!='4' or settings.get('sparse_infill_density')!='35%': raise ValueError('wrong wall/infill')
    if not any(line.startswith('G1 ') and ' E' in line for line in gcode.splitlines() if not line.startswith(';')): raise ValueError('no extrusion')
    report=json.loads((out/'result.json').read_text())
    plates=report.get('sliced_plates') or []
    print('SPINDLE_RESULT',json.dumps(report,sort_keys=True)[:6500],flush=True)
    if report.get('return_code')!=0 or len(plates)!=1 or plates[0].get('warning_message'): raise ValueError('slicer result/warning')
    objs=plates[0].get('objects') or []
    if len(objs)!=1 or int(objs[0].get('triangle_count',-1))!=9660: raise ValueError('slicer geometry mismatch')
    b=objs[0].get('bbox') or {}
    if not (0<float(b.get('height',0))<=180): raise ValueError('invalid print height')
    audit=dict(source_sha256=SOURCE_SHA,output_sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),bytes=dest.stat().st_size,filename=OUTPUT,printer='Bambu Lab A1 mini',nozzle_mm='0.4',material='PLA',prototype_only=True,parts=7,triangles=9660,bed='Textured PEI Plate',nozzle_c=220,bed_c=60,walls=4,infill='35%',status='SLICED_NOT_PRINTED',estimated_seconds=plates[0].get('total_predication'))
    (out/'spindle_audit.json').write_text(json.dumps(audit,indent=2,sort_keys=True)+'\n')
    print('SPINDLE_SLICED_VERIFIED',json.dumps(audit),flush=True)
if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,subprocess.SubprocessError,zipfile.BadZipFile,ET.ParseError) as e:
        print('SLICING_BLOCKED',type(e).__name__,str(e),file=sys.stderr,flush=True)
        sys.exit(1)
