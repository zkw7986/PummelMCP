from __future__ import annotations
import shutil
from pathlib import Path
from pummelmcp.pmh import PMHScene, DecodeConfidence, duplicate_leaf, read_pmh
from pummelmcp.pmh.scene_identity import classify_scene_identity, semantic_scene_fingerprint

def _pair(tmp_path:Path, source:Path):
    a=tmp_path/'before.scene';b=tmp_path/'after.scene';shutil.copyfile(source,a);shutil.copyfile(source,b);return a,b

def test_raw_sha_equal_is_exact(tmp_path,main_scene_path):
    a,b=_pair(tmp_path,main_scene_path);assert classify_scene_identity(a,b)['status']=='EXACT_SCENE_MATCH'

def test_known_editor_normalization_is_semantic_match(tmp_path,main_scene_path):
    a,b=_pair(tmp_path,main_scene_path);o=read_pmh(b).find_game_object('PlayerSpawn_0');p=o.get_component('ModTransform').get_field('position').value
    PMHScene.load(b).set_position(o.guid,x=p.x+0.4e-6,backup=False)
    assert classify_scene_identity(a,b)['status']=='SEMANTIC_SCENE_MATCH'

def test_excessive_transform_change_is_stale(tmp_path,main_scene_path):
    a,b=_pair(tmp_path,main_scene_path);o=read_pmh(b).find_game_object('PlayerSpawn_0');p=o.get_component('ModTransform').get_field('position').value
    PMHScene.load(b).set_position(o.guid,x=p.x+1e-3,backup=False)
    assert classify_scene_identity(a,b)['status']=='STALE_PLAYTEST_SCENE'

def test_hierarchy_change_is_stale(tmp_path,main_scene_path):
    a,b=_pair(tmp_path,main_scene_path);duplicate_leaf(b,'PlayerSpawn_0',backup=False)
    assert classify_scene_identity(a,b)['status']=='STALE_PLAYTEST_SCENE'

def _replace_same_size(path:Path,left:bytes,right:bytes):
    raw=path.read_bytes();assert len(left)==len(right) and left in raw;path.write_bytes(raw.replace(left,right,1))

def test_action_change_is_stale(tmp_path):
    source=Path(__file__).parent/'fixtures'/'Stage13DActionTemplate.scene';a,b=_pair(tmp_path,source)
    _replace_same_size(b,b'SpawnPrefabAction',b'SpawnPrefabActioN')
    assert classify_scene_identity(a,b)['status']=='STALE_PLAYTEST_SCENE'

def test_prefab_reference_change_is_stale(tmp_path):
    source=Path(__file__).parent/'fixtures'/'Stage13DActionTemplate.scene';a,b=_pair(tmp_path,source)
    _replace_same_size(b,b'Prefab_0',b'Prefab_1')
    assert classify_scene_identity(a,b)['status']=='STALE_PLAYTEST_SCENE'

def test_unknown_payload_change_is_stale(tmp_path,main_scene_path):
    a,b=_pair(tmp_path,main_scene_path);scene=read_pmh(b)
    field=next(f for o in scene.walk() for c in o.components for f in c.fields if f.confidence is DecodeConfidence.UNKNOWN and f.source_span and f.raw)
    raw=bytearray(b.read_bytes());i=field.source_span.start;raw[i]^=1;b.write_bytes(raw)
    assert classify_scene_identity(a,b)['status']=='STALE_PLAYTEST_SCENE'
