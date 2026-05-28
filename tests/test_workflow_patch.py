"""Tests for comfyui/workflow_patch.py"""

import pytest

from assetclaw_matting.comfyui.workflow_patch import (
    find_load_image_nodes,
    find_save_image_outputs,
    patch_load_image,
)


def _make_workflow(with_load: bool = True, extra_load: bool = False) -> dict:
    workflow = {
        "1": {
            "class_type": "KSampler",
            "inputs": {"steps": 20},
        },
        "3": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "output"},
        },
    }
    if with_load:
        workflow["2"] = {
            "class_type": "LoadImage",
            "inputs": {"image": "old_file.png"},
        }
    if extra_load:
        workflow["5"] = {
            "class_type": "LoadImage",
            "inputs": {"image": "another.png"},
        }
    return workflow


def test_find_load_image_nodes_found():
    wf = _make_workflow()
    ids = find_load_image_nodes(wf)
    assert "2" in ids


def test_find_load_image_nodes_missing():
    wf = _make_workflow(with_load=False)
    assert find_load_image_nodes(wf) == []


def test_patch_load_image_replaces():
    wf = _make_workflow()
    patched = patch_load_image(wf, "new_image.png")
    assert patched["2"]["inputs"]["image"] == "new_image.png"


def test_patch_load_image_raises_on_missing():
    wf = _make_workflow(with_load=False)
    with pytest.raises(ValueError, match="No LoadImage node"):
        patch_load_image(wf, "x.png")


def test_patch_load_image_multiple_warns(caplog):
    wf = _make_workflow(with_load=True, extra_load=True)
    import logging
    with caplog.at_level(logging.WARNING):
        patched = patch_load_image(wf, "new.png")
    assert "Multiple LoadImage" in caplog.text
    # First node (sorted by key) should be patched
    patched_ids = [k for k in patched if patched[k].get("class_type") == "LoadImage"
                   and patched[k]["inputs"]["image"] == "new.png"]
    assert len(patched_ids) == 1


def test_find_save_image_outputs():
    prompt_id = "abc-123"
    history = {
        prompt_id: {
            "outputs": {
                "3": {
                    "images": [
                        {"filename": "output_00001_.png", "subfolder": "", "type": "output"}
                    ]
                }
            },
            "status": {"completed": True},
        }
    }
    outputs = find_save_image_outputs(history, prompt_id)
    assert outputs[0]["filename"] == "output_00001_.png"


def test_find_save_image_outputs_missing_raises():
    prompt_id = "abc-123"
    history = {prompt_id: {"outputs": {}, "status": {"completed": True}}}
    with pytest.raises(ValueError, match="No SaveImage outputs"):
        find_save_image_outputs(history, prompt_id)
