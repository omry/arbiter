from __future__ import annotations
import hashlib, json, os, signal, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path
from dataclasses import dataclass, field
from contextlib import ExitStack
from urllib.parse import urlsplit, parse_qs
sys.path.insert(0, str(Path(__file__).resolve().parents[5] / 'server/src'))

def connect(p):
    c=sqlite3.connect(p, timeout=1, isolation_level=None)
    c.execute('PRAGMA synchronous=FULL'); c.execute('PRAGMA foreign_keys=ON')
    return c

def reap(p):
    try:
        if p.poll() is None:
            p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=5)
    finally:
        if p.stdout is not None:
            p.stdout.close()

def spawn(processes, *args, **kwargs):
    p = subprocess.Popen(*args, **kwargs)
    processes.callback(reap, p)
    return p

def child(mode, root):
    r=Path(root)
    if mode=='transaction':
        c=connect(r/'state.db');c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT INTO seen VALUES (?,?)',('uncommitted','user:synthetic'))
        c.execute('UPDATE cursor SET value=99');(r/'ready').touch()
        while True: time.sleep(.01)
    if mode=='lock':
        import fcntl
        with open(r/'poll.lock','a+b') as f:
            try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: print('excluded');return
            print('acquired')
    if mode=='reader':
        seen=set()
        while not (r/'stop').exists():
            b=(r/'binding').read_bytes();assert b in [b'A'*1024,b'B'*1024];seen.add(b[:1].decode())
            (r/('read_'+b[:1].decode())).touch()
        print(json.dumps(sorted(seen)))
    if mode=='artifact':
        from arbiter_server.artifacts import ArtifactStore, ArtifactConsumed, ArtifactNotFound
        d=json.loads((r/'descriptor.json').read_text())
        s=ArtifactStore(root=r/'artifacts',base_url='http://127.0.0.1/artifacts')
        try:
            if os.environ.get('PROBE_READ_MEMORY'):
                with s._storage_lock():
                    folder,m=s._validated_artifact(d['id'],d['nonce'])
                    data=(folder/'payload').read_bytes()
                    assert len(data)==m['size'] and hashlib.sha256(data).hexdigest()==m['sha256']
                    s._claim_artifact(folder,d['id']);s._record_access(folder,m,consume=True)
            else: s.open_once(d['id'],d['nonce'])
            print('consumed')
        except (ArtifactConsumed,ArtifactNotFound): print('unavailable')

if __name__=='__main__':
    if len(sys.argv)>1: child(sys.argv[1],sys.argv[2]);sys.exit()
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    results={}
    from arbiter_server.services import operation_input_schema, _validate_operation_arguments
    from arbiter_server.errors import OperationValidationError
    @dataclass
    class Text: body:str
    @dataclass
    class Input: text:Text=field(default_factory=lambda:Text(''))
    assert _validate_operation_arguments('probe',Input,{'text':{'body':'hello'}})=={'text':{'body':'hello'}}
    try: _validate_operation_arguments('probe',Input,{'text':{'unknown':'x'}})
    except OperationValidationError: pass
    else: raise AssertionError('unknown nested field accepted')
    try: operation_input_schema(Input)
    except TypeError: results['nested_schema']='structured validation works; schema generator branch is absent'
    else: raise AssertionError('missing branch expectation changed')
    with tempfile.TemporaryDirectory(prefix='whatsapp-fresh-probe-') as tmp, ExitStack() as processes:
        r=Path(tmp);c=connect(r/'state.db')
        c.executescript('CREATE TABLE cursor(value INTEGER);INSERT INTO cursor VALUES(0);CREATE TABLE seen(id TEXT PRIMARY KEY,participant TEXT);CREATE TABLE reply(id TEXT PRIMARY KEY,state TEXT,payload TEXT);')
        p=spawn(processes,[sys.executable,__file__,'transaction',tmp])
        end=time.monotonic()+5
        while not (r/'ready').exists():
            assert p.poll() is None and time.monotonic()<end;time.sleep(.01)
        p.kill();p.wait(timeout=5)
        assert c.execute('SELECT value FROM cursor').fetchone()==(0,)
        assert c.execute('SELECT count(*) FROM seen').fetchone()==(0,)
        c.execute('BEGIN IMMEDIATE');c.execute('INSERT INTO seen VALUES (?,?)',('source','user:synthetic'));c.commit()
        assert c.execute('SELECT value FROM cursor').fetchone()==(0,)
        c.execute('INSERT INTO reply VALUES (?,?,?)',('source','pending','hash'))
        c.close();c=connect(r/'state.db');assert c.execute('SELECT state FROM reply').fetchone()==('pending',)
        c.execute("UPDATE reply SET state='unknown' WHERE state='pending'")
        assert c.execute('SELECT state FROM reply').fetchone()==('unknown',)
        results['sqlite']='uncommitted cursor/source rollback together; replay source does not advance cursor; committed reservation survives restart'
        import fcntl
        with open(r/'poll.lock','a+b') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            assert subprocess.check_output([sys.executable,__file__,'lock',tmp],text=True).strip()=='excluded'
        assert subprocess.check_output([sys.executable,__file__,'lock',tmp],text=True).strip()=='acquired'
        results['process_lock']='second process excluded while held; released after close'
        (r/'binding').write_bytes(b'A'*1024);os.chmod(r/'binding',0o600)
        p=spawn(processes,[sys.executable,__file__,'reader',tmp],stdout=subprocess.PIPE,text=True)
        end=time.monotonic()+5
        while not (r/'read_A').exists():
            assert p.poll() is None and time.monotonic()<end;time.sleep(.01)
        for i in range(50):
            fd=os.open(r/'pending',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f: f.write((b'A' if i%2==0 else b'B')*1024);f.flush();os.fsync(f.fileno())
            os.replace(r/'pending',r/'binding')
            fd=os.open(r,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
        end=time.monotonic()+5
        while not (r/'read_B').exists():
            assert p.poll() is None and time.monotonic()<end;time.sleep(.01)
        (r/'stop').touch();out=p.communicate(timeout=5)[0];assert p.returncode==0 and json.loads(out)==['A','B']
        assert (r/'binding').stat().st_mode&0o777==0o600
        results['atomic_record']='reader observed only complete versions; 50 same-directory replacements preserved mode 0600; not a power-loss test'
        from arbiter_server.artifacts import ArtifactStore
        s=ArtifactStore(root=r/'artifacts',base_url='http://127.0.0.1/artifacts')
        d=s.create(plugin='probe',content=b'x'*1048576,filename=None,content_type='application/octet-stream',source={})
        n=parse_qs(urlsplit(d.url).query)['nonce'][0]
        (r/'descriptor.json').write_text(json.dumps({'id':d.id,'nonce':n}))
        ps=[spawn(processes,[sys.executable,__file__,'artifact',tmp],stdout=subprocess.PIPE,text=True,env={**os.environ,**({'PROBE_READ_MEMORY':'1'} if i==0 else {})}) for i in range(2)]
        out=[p.communicate(timeout=5)[0].strip() for p in ps]
        assert all(p.returncode==0 for p in ps) and sorted(out)==['consumed','unavailable'],out
        d=s.create(plugin='probe',content=b'y'*1048576,filename=None,content_type='application/octet-stream',source={})
        n=parse_qs(urlsplit(d.url).query)['nonce'][0]
        (r/'descriptor.json').write_text(json.dumps({'id':d.id,'nonce':n}))
        p=spawn(processes,[sys.executable,__file__,'artifact',tmp],stdout=subprocess.PIPE,text=True,env={**os.environ,'PROBE_READ_MEMORY':'1'})
        out=p.communicate(timeout=5)[0].strip()
        assert p.returncode==0 and out=='consumed',out
        results['artifact']='standalone bounded in-memory consumption succeeds; competing in-memory read and existing open_once admit exactly one consumer; synthetic 1 MiB; not implemented adapter'
    print(json.dumps({'platform':sys.platform,'results':results},indent=2))
