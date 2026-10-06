from pathlib import Path
import anyio
import pytest
from types import SimpleNamespace
from mcp.server.mcpserver.exceptions import ToolError
from pummelmcp.workflow import GameWorkflow
from pummelmcp.mcp_server import create_server

def proposal(target):
    return dict(game_directory=str(target), requested_rules=['One native button'],
        capability_assessment=[dict(requested_rule='One native button', status='supported',
            editor_evidence='Native ModTrigger OnHitActions', implementation_or_boundary='A single-use hit trigger')],
        gameplay_plan=dict(flow=['Spawn', 'Hit button'], rules_and_parameters={'one_use': True},
            scene_and_assets=['Primitive button'], limitations=['Playtest pending'], playtest_checks=['Second hit does nothing']))

def call(server, name, args):
    async def invoke():return await server.call_tool(name,args)
    try:return anyio.run(invoke)
    except ToolError as exc:return SimpleNamespace(is_error=True,error=str(exc))

def approve(state, target):
    review=state.submit(**proposal(target))
    state.approve(review['review_sha256'],True,'Approve this exact plan')

def test_review_required_stale_and_rejection(tmp_path):
    w=GameWorkflow();w.authorize(tmp_path);target=tmp_path/'Game'
    with pytest.raises(ValueError,match='USER_GAMEPLAY_REVIEW_REQUIRED'):w.require_write(target)
    review=w.submit(**proposal(target))
    with pytest.raises(ValueError,match='STALE'):w.approve('incorrect',True,'yes')
    assert not w.approve(review['review_sha256'],False,'reject')['approved']
    with pytest.raises(ValueError,match='DIRECT_USER'):w.approve(review['review_sha256'],True,'')
    w.approve(review['review_sha256'],True,'I approve')
    w.require_write(target,creation=True)
    w.submit(**proposal(target))
    with pytest.raises(ValueError,match='USER_GAMEPLAY_REVIEW_REQUIRED'):w.require_write(target)

def test_bound_original_and_explicit_separate_game(tmp_path):
    w=GameWorkflow();w.authorize(tmp_path);target=tmp_path/'Original'
    approve(w,target);target.mkdir();w.wrote(target)
    w.require_write(target)
    with pytest.raises(ValueError,match='ITERATE_EXISTING'):w.require_write(target,creation=True)
    with pytest.raises(ValueError,match='ITERATE_EXISTING'):w.submit(**proposal(tmp_path/'NewVersion'))
    review=w.submit(**proposal(tmp_path/'Separate'),user_requested_separate_game=True)
    with pytest.raises(ValueError,match='USER_GAMEPLAY_REVIEW_REQUIRED'):w.require_write(tmp_path/'Separate',creation=True)
    w.approve(review['review_sha256'],True,'Create a separate game')
    w.require_write(tmp_path/'Separate',creation=True)
    # Planning a separate game's first creation does not suspend original edits.
    w.require_write(target)

def test_assessment_and_scope_fail_closed(tmp_path):
    w=GameWorkflow();w.authorize(tmp_path)
    with pytest.raises(ValueError,match='IMMEDIATE_WORKSHOP'):w.submit(**proposal(tmp_path/'nested'/'Game'))
    p=proposal(tmp_path/'Game');p['capability_assessment']=[]
    with pytest.raises(ValueError,match='ASSESS_EVERY'):w.submit(**p)
    p=proposal(tmp_path/'Game');p['capability_assessment'][0]['editor_evidence']=''
    with pytest.raises(ValueError,match='EVIDENCE'):w.submit(**p)
    p=proposal(tmp_path/'Game');p['gameplay_plan'].pop('limitations')
    with pytest.raises(ValueError,match='DETAILED'):w.submit(**p)

def test_mcp_blocks_creation_and_replacement_then_allows_in_place(tmp_path):
    workshop=tmp_path/'WorkshopMods';workshop.mkdir();server=create_server(allowed_root=tmp_path)
    args={'template_id':'simple-arena','destination':'Original'}
    assert call(server,'initialize_mod_from_template',args).is_error
    assert not list(workshop.iterdir())
    assert not call(server,'authorize_workshop_directory',dict(workshop_directory=str(workshop),user_granted_write_permission=True)).is_error
    assert call(server,'initialize_mod_from_template',args).is_error
    assert not list(workshop.iterdir())
    review=call(server,'submit_gameplay_review',proposal(workshop/'Original')).structured_content
    assert call(server,'initialize_mod_from_template',args).is_error
    assert not call(server,'approve_gameplay_review',dict(review_sha256=review['review_sha256'],user_approved=True,user_reply='I approve this exact plan')).is_error
    result=call(server,'initialize_mod_from_template',args)
    assert not result.is_error
    scene=Path(result.structured_content['scene_path']);before=scene.read_bytes()
    assert call(server,'initialize_mod_from_template',args).is_error
    assert call(server,'initialize_mod_from_template',{**args,'destination':'NewVersion'}).is_error
    state=call(server,'get_game_workflow_status',{}).structured_content
    assert state['delivered_game_directory']==str(workshop/'Original')
    assert not (workshop/'NewVersion').exists() and scene.read_bytes()==before
    # Optional iteration planning no longer requires a second human approval.
    objects=call(server,'list_scene_objects',{'scene_path':str(scene)}).structured_content
    target=objects['objects'][0]['guid']
    write={'scene_path':str(scene),'identifier':target,'position':{'x':2.0}}
    review=call(server,'submit_gameplay_review',proposal(workshop/'Original')).structured_content
    assert review['status']=='ITERATION_READY' and not review['requires_user_review']
    updated=call(server,'set_transform',write)
    assert not updated.is_error
    assert scene.read_bytes()!=before and list(workshop.iterdir())==[workshop/'Original']

def test_resume_existing_mod_after_restart_without_review(tmp_path):
    workshop=tmp_path/'WorkshopMods';workshop.mkdir()
    server=create_server(allowed_root=tmp_path)
    authorization=dict(workshop_directory=str(workshop),user_granted_write_permission=True)
    assert not call(server,'authorize_workshop_directory',authorization).is_error
    review=call(server,'submit_gameplay_review',proposal(workshop/'Original')).structured_content
    call(server,'approve_gameplay_review',dict(review_sha256=review['review_sha256'],user_approved=True,user_reply='I approve initial creation'))
    result=call(server,'initialize_mod_from_template',{'template_id':'simple-arena','destination':'Original'})
    assert not result.is_error
    scene=Path(result.structured_content['scene_path']);before=scene.read_bytes()
    # A new server has no old review or binding, but the original Mod still exists.
    resumed=create_server(allowed_root=tmp_path)
    assert not call(resumed,'authorize_workshop_directory',authorization).is_error
    objects=call(resumed,'list_scene_objects',{'scene_path':str(scene)}).structured_content
    result=call(resumed,'set_transform',{'scene_path':str(scene),'identifier':objects['objects'][0]['guid'],'position':{'x':3.0}})
    assert not result.is_error
    state=call(resumed,'get_game_workflow_status',{}).structured_content
    assert state['pending_review'] is None and not state['user_review_approved']
    assert state['delivered_game_directory']==str(workshop/'Original')
    assert scene.read_bytes()!=before
    # New-game creation and replacing the existing Mod remain forbidden.
    assert call(resumed,'initialize_mod_from_template',{'template_id':'simple-arena','destination':'Separate'}).is_error
    assert call(resumed,'initialize_mod_from_template',{'template_id':'simple-arena','destination':'Original'}).is_error

def test_empty_directory_is_not_an_initialized_game(tmp_path):
    state=GameWorkflow();state.authorize(tmp_path)
    empty=tmp_path/'Empty';empty.mkdir()
    with pytest.raises(ValueError,match='USER_GAMEPLAY_REVIEW_REQUIRED'):
        state.require_write(empty)
    partial=tmp_path/'Partial';(partial/'Data').mkdir(parents=True)
    (partial/'Data/MainScene.scene').write_bytes(b'PMH')
    (partial/'Data/Meta.json').write_text('{}')
    with pytest.raises(ValueError,match='USER_GAMEPLAY_REVIEW_REQUIRED'):
        state.require_write(partial)

def test_iteration_does_not_drop_directory_or_target_protection(tmp_path):
    state=GameWorkflow()
    with pytest.raises(ValueError,match='WORKSHOP_AUTHORIZATION_REQUIRED'):
        state.require_write(tmp_path/'Original')
    state.authorize(tmp_path)
    target=tmp_path/'Original';target.mkdir();state.wrote(target)
    state.require_write(target)
    with pytest.raises(ValueError,match='IMMEDIATE_WORKSHOP'):
        state.require_write(tmp_path/'nested'/'Outside')
    with pytest.raises(ValueError,match='ITERATE_EXISTING'):
        state.require_write(tmp_path/'Another')
