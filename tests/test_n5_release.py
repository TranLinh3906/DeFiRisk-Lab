"""Frozen input, parent provenance and path-boundary release guards."""
import json
from pathlib import Path
from uuid import uuid4
import shutil
import pytest
from src.models.n5_release import sha256,verify_release


@pytest.fixture
def frozen():
    # Python's Windows 0700 temp ACL omits the sandbox identity. Inherit the
    # workspace ACL with normal mkdir, then clean only our verified directory.
    base=Path(__file__).resolve().parents[1]/'outputs/n5_release_audit_20261026/test_tmp'
    base.mkdir(exist_ok=True,parents=True)
    tmp_path=base/('integrity_'+uuid4().hex)
    tmp_path.mkdir()
    assert tmp_path.resolve().is_relative_to(base.resolve())
    try:
        root=tmp_path/'release';root.mkdir()
        (root/'inputs').mkdir();(root/'results').mkdir();(root/'provenance').mkdir()
        live=tmp_path/'live.csv';live.write_bytes(b'price\n1\n')
        input_file=root/'inputs/price.csv';input_file.write_bytes(live.read_bytes())
        result=root/'results/loss.csv';result.write_bytes(b'loss\n0\n')
        parent=dict(inputs=[dict(path=str(live),sha256=sha256(live))],files=[dict(path='loss.csv',sha256=sha256(result))])
        parent_path=root/'provenance/analysis_manifest.json';parent_path.write_text(json.dumps(parent),encoding='utf-8')
        manifest=dict(version='n5_official_release_v1',snapshot_blocks={'B1':20458637},
            source_analysis_manifest='provenance/analysis_manifest.json',source_analysis_manifest_sha256=sha256(parent_path),
            inputs=[dict(path='inputs/price.csv',sha256=sha256(input_file),original_source_path=str(live))],
            files=[dict(path=p.relative_to(root).as_posix(),sha256=sha256(p)) for p in [input_file,result,parent_path]])
        (root/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        yield root,live
    finally:
        assert tmp_path.resolve().is_relative_to(base.resolve())
        shutil.rmtree(tmp_path)


def test_release_uses_frozen_input_not_mutable_workspace(frozen):
    root,live=frozen
    assert verify_release(root)==[]
    live.write_bytes(b'price\n999\n')
    assert verify_release(root)==[]
    (root/'inputs/price.csv').write_bytes(b'price\n999\n')
    assert 'hash:inputs/price.csv' in verify_release(root)


def test_input_hash_must_be_locked_in_file_inventory(frozen):
    root,_=frozen;m=json.loads((root/'manifest.json').read_text())
    m['inputs'][0]['sha256']='0'*64
    (root/'manifest.json').write_text(json.dumps(m),encoding='utf-8')
    assert 'input_not_locked:inputs/price.csv' in verify_release(root)


def test_rehashing_changed_result_does_not_bless_wrong_run(frozen):
    root,_=frozen
    (root/'results/loss.csv').write_bytes(b'loss\n100\n')
    m=json.loads((root/'manifest.json').read_text())
    next(r for r in m['files'] if r['path']=='results/loss.csv')['sha256']=sha256(root/'results/loss.csv')
    (root/'manifest.json').write_text(json.dumps(m),encoding='utf-8')
    assert 'result_parent_mismatch:loss.csv' in verify_release(root)


def test_release_rejects_path_escape(frozen):
    root,_=frozen;m=json.loads((root/'manifest.json').read_text())
    m['files'][0]['path']='../live.csv'
    (root/'manifest.json').write_text(json.dumps(m),encoding='utf-8')
    with pytest.raises(ValueError,match='escapes'):verify_release(root)
