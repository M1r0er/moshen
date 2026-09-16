"""星图分析「暂停 / 继续 / 停止」回归测试

覆盖四件事：
1. 暂停闸门在未暂停时直接放行，不产生多余进度事件
2. 暂停时闸门挂起并下发 paused 事件（completed 为即将开始的批次序号），继续后放行并下发 running
3. 停在闸门上的任务可以被取消（停止已暂停的任务不能卡死）
4. 没有任务在跑时，三个控制接口返回 false 而不是报错

运行（仓库根目录，无需额外依赖）：
    python -m unittest discover -s backend/tests -t backend -v
"""
import asyncio
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from routes import relations  # noqa: E402


def _events(project_id):
    """取出该项目已广播的结构化进度事件"""
    info = relations._tasks.get(project_id)
    return list(info["progress"]) if info else []


class PauseGateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.project_id = "test-project"
        relations._tasks.clear()
        relations._tasks[self.project_id] = relations._new_task_info()
        self.info = relations._tasks[self.project_id]

    def tearDown(self):
        relations._tasks.clear()

    # ---------- 辅助 ----------
    async def _wait_until(self, cond, timeout=1.0):
        """轮询等待条件成立，避免依赖固定 sleep 时长造成偶发失败"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if cond():
                return True
            await asyncio.sleep(0.005)
        return cond()

    # ---------- 用例 ----------
    async def test_gate_passes_when_not_paused(self):
        """未暂停：闸门立即返回，且不写任何进度事件"""
        task = asyncio.create_task(relations._pause_gate(self.project_id, 0))
        await asyncio.wait_for(task, timeout=0.5)
        self.assertEqual(_events(self.project_id), [])

    async def test_gate_blocks_then_resumes(self):
        """暂停：闸门挂起并下发 paused；继续：放行并下发 running"""
        self.info["resume_event"].clear()
        task = asyncio.create_task(relations._pause_gate(self.project_id, 3))

        blocked = await self._wait_until(lambda: len(_events(self.project_id)) >= 1)
        self.assertTrue(blocked, "暂停后应下发 paused 事件")
        self.assertFalse(task.done(), "暂停后闸门应挂起，不应自行放行")

        paused = _events(self.project_id)[0]
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["phase"], "paused")
        self.assertEqual(paused["completed"], 3)
        self.assertEqual(paused["task_key"], "relations.analyze")

        # 暂停期间被消耗的时间不应影响后续批次：继续后闸门放行
        self.info["resume_event"].set()
        await asyncio.wait_for(task, timeout=1.0)

        statuses = [e["status"] for e in _events(self.project_id)]
        self.assertEqual(statuses, ["paused", "running"])
        self.assertEqual(_events(self.project_id)[1]["completed"], 3)

    async def test_gate_can_be_cancelled_while_paused(self):
        """停在闸门上的任务可被取消 —— 停止已暂停的任务不能卡死"""
        self.info["resume_event"].clear()
        task = asyncio.create_task(relations._pause_gate(self.project_id, 0))
        self.assertTrue(await self._wait_until(lambda: not task.done() and _events(self.project_id)))

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(task.cancelled())


class ControlEndpointTest(unittest.IsolatedAsyncioTestCase):
    """没有任务在跑时，控制接口必须优雅返回而不是抛异常"""

    async def test_control_endpoints_without_running_task(self):
        relations._tasks.clear()
        project_id = "no-such-task"

        paused = await relations.pause_analyze(project_id)
        self.assertFalse(paused["paused"])
        self.assertIn("没有正在进行", paused["message"])

        resumed = await relations.resume_analyze(project_id)
        self.assertFalse(resumed["resumed"])

        cancelled = await relations.cancel_analyze(project_id)
        self.assertFalse(cancelled["cancelled"])

    async def test_cancel_releases_paused_task(self):
        """停止一个已暂停的任务：先解除暂停，取消才能送达"""
        relations._tasks.clear()
        project_id = "paused-task"
        info = relations._new_task_info()
        relations._tasks[project_id] = info
        info["resume_event"].clear()

        gate = asyncio.create_task(relations._pause_gate(project_id, 0))
        info["task"] = gate
        await asyncio.sleep(0.02)
        self.assertFalse(gate.done())

        result = await relations.cancel_analyze(project_id)
        self.assertTrue(result["cancelled"])
        # resume_event 被置位，且任务已被取消
        self.assertTrue(info["resume_event"].is_set())
        for _ in range(50):
            if gate.cancelled() or gate.done():
                break
            await asyncio.sleep(0.005)
        self.assertTrue(gate.done())
        relations._tasks.clear()


if __name__ == "__main__":
    unittest.main()
