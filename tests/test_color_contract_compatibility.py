"""锁定政策实施前的旧 exact 定义；测试指纹不是 Runtime 或媒体 authority。

这些值于新增色彩政策代码前由本工作树导出，防止抽取共用计算时悄悄改变旧 Schema/执行器。
"""

import hashlib
import json

# ruff: noqa: E501
from zniku.prepared_source.definitions import built_in_overlap_definitions, external_definition
from zniku.source_preparation.definitions import source_preparation_definitions

FROZEN = {
    "zniku.source_preparation.source": "5d35662b5103cc5365abbb0e37a345467ab8508c1067975d0320f1d653bbc8c6",
    "zniku.source_preparation.diagnostics": "58d588658acd7527002018de75cc9f2b5b837aa457f1f62277791c03c8b98a56",
    "zniku.source_preparation.video_prepare.t1": "ff904805a099b072fe2bae3461adbd570c0857fd2bb0088af5504bd274020f56",
    "zniku.source_preparation.video_repair.external.mkv": "701a17bc1c983a1ae06170203d1393910c44d88674aae0e1bd53fc21c66af254",
    "zniku.source_preparation.video_repair.external.mp4": "fe66d481c042a4e1bb6d26f0b3bb2987d1cdcd4753821274c420afd6d56b6296",
    "zniku.source_preparation.video_repair.external.mov": "539c891516f4f93dc43eb13241a0c5156d9984d5f2d8f4923f22ddf64ff12071",
    "zniku.source_preparation.admission": "b072ed734b4e7e81028a908d1f0a1ed9cc6c627dcb46d99bf5d52d1107481b56",
    "zniku.prepared.overlap.split.leaves.1": "afe84fbb5d8ecd6c2fce3c1495fbbd45945a00bd6e3279de94a78a911b3dd38e",
    "zniku.prepared.overlap.enhancement.external": "c9d0cf259baa7d5cc1400109b444c50df88a09ab45bb5132dbd7581347334f2e",
    "zniku.prepared.overlap.merge_video": "a537bf05569b155cfbd765e57e7de3e6e3b9f30721eb275ffb540002c23e13f7",
    "zniku.prepared.overlap.fi_context": "c59b6d8cbdb9ec780e492dbd2eb8a6ec7bb5ecd0a51d768bfe2b71e20c7adb95",
    "zniku.prepared.overlap.frame_interpolation.external": "b0dd711c900d96bf25d77c0c4d5e865c48242bdc742ceeb2aa6da5b20cea2122",
    "zniku.prepared.overlap.fi_crop": "4534f9a504ba20314d357e86e185ad16e3bc05bf8131cbc1aa5c5b7da04a5e15",
    "zniku.prepared.overlap.program_encode": "587710e9c921210d9de9e7448d9967a1db0dd94a1a6230451c747d5dc9cada6c",
    "zniku.prepared.overlap.final_mux": "a860dab14428140b9fbb0faaa4f6cf0593b87da59b681774f570c31f69d60198",
    "zniku.prepared_source.external.mp4": "230293869b09f6ed79e968012c8c3396507c6229d5a432809fad9e40763c93c2",
    "zniku.prepared_source.external.mov": "95def5ea5fe80384d27a2185ef1a2e2517561b5b5d792e70f0e97b41f41d7d80",
    "zniku.prepared_source.external.mkv": "38dd14d8bec8b5c331fb1ea47e6a7ef72d8d4510afbe0ce4c724ed5b195b7010",
}


def test_original_034_exact_definitions_are_unchanged() -> None:
    """新产品可并列提供色彩合同，但旧 snapshot 的完整定义仍逐字等价。"""
    definitions = (
        *source_preparation_definitions(),
        *built_in_overlap_definitions(),
        external_definition("mp4"),
        external_definition("mov"),
        external_definition("mkv"),
    )
    assert {d.version for d in definitions} == {"0.3.4"}
    assert {
        d.type_id: hashlib.sha256(
            json.dumps(d.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        for d in definitions
    } == FROZEN
