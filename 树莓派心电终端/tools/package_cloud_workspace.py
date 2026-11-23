"""Create an allowlisted terminal bundle without recordings, caches or credentials."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

ROOT=Path(__file__).resolve().parents[1]


def build():
    names=json.loads((ROOT/'tools/cloud_workspace_files.json').read_text(encoding='utf-8'))
    contents={}
    for name in sorted(set(names)):
        relative=PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Invalid bundle path')
        file=ROOT.joinpath(*relative.parts)
        if file.is_symlink() or not file.is_file() or not file.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError('Not a regular source file: '+name)
        contents[name]=file.read_bytes()
    hashes={name:hashlib.sha256(body).hexdigest() for name,body in contents.items()}
    contents['CLOUD_WORKSPACE_MANIFEST.json']=json.dumps(hashes,ensure_ascii=False,indent=2).encode('utf-8')
    output=ROOT/'dist/ecg-cloud-workspace.tar.gz';temporary=output.with_suffix('.tmp')
    output.parent.mkdir(exist_ok=True)
    with tarfile.open(temporary,'w:gz') as archive:
        for name,body in contents.items():
            info=tarfile.TarInfo('ecg_terminal/'+name);info.size=len(body);info.mtime=0
            info.mode=0o755 if name.endswith('.sh') else 0o644
            archive.addfile(info,io.BytesIO(body))
    with tarfile.open(temporary) as archive:
        assert set(archive.getnames())=={'ecg_terminal/'+name for name in contents}
        for name,body in contents.items():assert archive.extractfile('ecg_terminal/'+name).read()==body
    temporary.replace(output)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(digest+'  '+output.name+'\n',encoding='ascii')
    print(f'{output.name}: {len(hashes)} source files; sha256={digest}')


if __name__=='__main__':build()
