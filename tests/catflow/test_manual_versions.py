from __future__ import annotations

from catflow.application.service import ProjectCreate, StoryCreateCommand, StudioService
from catflow.infrastructure.memory_repository import MemoryStudioRepository


def test_manual_story_versions_are_immutable_and_can_be_reactivated() -> None:
    service = StudioService(MemoryStudioRepository())
    project = service.create_project(
        ProjectCreate(title="手动故事", theme="整理早餐", targetDurationSeconds=10)
    )
    first = service.create_story(
        project.id,
        StoryCreateCommand(
            title="整理早餐 A",
            body="孩子收起餐垫，猫咪把滚到桌边的小勺推回来。",
            microEvent={
                "trigger": "小勺滚向桌边",
                "childAction": "孩子伸手接住小勺",
                "catResponse": "猫咪用前爪轻轻推回小勺",
                "visibleChange": "早餐桌重新变整齐",
                "warmEnding": "孩子摸摸猫咪的头",
            },
            targetDurationSeconds=10,
            dialoguePolicy="none",
            environmentIntent="清晨餐桌暖光",
        ),
    )
    second = service.create_story(
        project.id,
        StoryCreateCommand(
            title="整理早餐 B",
            body="孩子叠好餐巾，猫咪安静守着最后一只杯子。",
            microEvent={
                "trigger": "餐巾散在桌面",
                "childAction": "孩子叠好餐巾",
                "catResponse": "猫咪坐在杯子旁观察",
                "visibleChange": "桌面恢复整齐",
                "warmEnding": "阳光落在一人一猫身上",
            },
            targetDurationSeconds=10,
            dialoguePolicy="none",
            environmentIntent="清晨餐桌暖光",
        ),
    )
    reactivated = service.activate_story(project.id, first.id)

    assert first.revision == 1
    assert second.revision == 2
    assert reactivated.id == first.id
    assert [story.active for story in service.list_stories(project.id)] == [False, True]
