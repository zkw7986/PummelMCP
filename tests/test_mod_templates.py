import json
from pathlib import Path

import pytest

from pummelmcp.mod_templates import TEMPLATE_ROOT, initialize_mod_from_template, list_mod_templates


def test_all_templates(tmp_path):
    for entry in list_mod_templates()['templates']:
        result = initialize_mod_from_template(entry['id'], entry['id'], allowed_root=tmp_path)
        target = Path(result['mod_path'])
        source = TEMPLATE_ROOT / entry['id']
        assert Path(result['scene_path']).read_bytes() == (source / 'Data/MainScene.scene').read_bytes()
        for asset in (source / 'Assets').rglob('*'):
            if asset.is_file() and asset.name != ".gitkeep":
                assert (target / asset.relative_to(source)).read_bytes() == asset.read_bytes()
        assert json.loads((target / 'Data/WorkshopItem.json').read_text())['publishedFileId'] == 0
        assert json.loads((target / 'Data/Meta.json').read_text())['Name'] == entry['id']
        with pytest.raises(ValueError, match='already exists'):
            initialize_mod_from_template(entry['id'], entry['id'], allowed_root=tmp_path)


def test_reject_escape_and_unknown(tmp_path):
    for destination in ('../outside', str(tmp_path)):
        with pytest.raises(ValueError, match='inside'):
            initialize_mod_from_template('simple-arena', destination, allowed_root=tmp_path)
    with pytest.raises(ValueError, match='Unknown'):
        initialize_mod_from_template('unknown', 'new', allowed_root=tmp_path)
