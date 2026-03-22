"""
World 热力聚合 + 数据清理 APScheduler 任务

聚合任务（每5分钟）：
  movement_events  →  heatmap_cells
  按 cell_x = x // 30, cell_y = y // 30 分桶，COUNT 后 UPSERT。

清理任务（每日）：
  90 天前 movement_events + social_events → DELETE。

使用 run_migration / auto-flush 时，任务中途失败不影响数据完整性。
"""
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, text

from app.database import SessionLocal

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="UTC")

CELL_SIZE = 30  # 与 WorldState.CELL_SIZE 保持一致
TTL_DAYS = 90


def _agg_cells():
    """
    从 movement_events 聚合到 heatmap_cells。
    按 (cell_x, cell_y) 分桶，UPSERT event_count。
    """
    db = SessionLocal()
    try:
        # 聚合最近 10 分钟未处理的 movement_events
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        cells = (
            db.query(
                text("x DIV :cell_size").label("cell_x"),
                text("y DIV :cell_size").label("cell_y"),
                func.count(text("*")).label("cnt"),
            )
            .filter(text("created_at >= :cutoff"))
            .params(cutoff=cutoff, cell_size=CELL_SIZE)
            .group_by(text("cell_x"), text("cell_y"))
            .all()
        )

        if not cells:
            return 0

        for row in cells:
            db.execute(
                text("""
                    INSERT INTO heatmap_cells (cell_x, cell_y, event_count, updated_at)
                    VALUES (:cx, :cy, :cnt, :now)
                    ON DUPLICATE KEY UPDATE
                        event_count = event_count + VALUES(event_count),
                        updated_at = VALUES(updated_at)
                """),
                {"cx": row.cell_x, "cy": row.cell_y, "cnt": row.cnt, "now": datetime.now(timezone.utc)},
            )

        db.commit()
        logger.info("aggregated %d heatmap cells", len(cells))
        return len(cells)
    except Exception:
        logger.exception("heatcell aggregation failed")
        db.rollback()
        return 0
    finally:
        db.close()


def _cleanup_old_events():
    """
    删除 90 天前的 movement_events 和 social_events。
    分批删除避免长时间锁表。
    """
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)
        batch = 5000
        total = 0

        # 批量删除 movement_events
        while True:
            ids = (
                db.query(text("id"))
                .select_from(text("movement_events"))
                .filter(text("created_at < :cutoff"))
                .limit(batch)
                .all()
            )
            if not ids:
                break
            id_list = [r[0] for r in ids]
            # 用原生 SQL DELETE 避免 ORM 批量删除开销
            res = db.execute(
                text("DELETE FROM movement_events WHERE id IN :ids"),
                {"ids": tuple(id_list)},
            )
            db.commit()
            total += res.rowcount
            if res.rowcount < batch:
                break

        # 批量删除 social_events
        while True:
            res = db.execute(
                text("DELETE FROM social_events WHERE created_at < :cutoff LIMIT :batch"),
                {"cutoff": cutoff, "batch": batch},
            )
            db.commit()
            total += res.rowcount
            if res.rowcount < batch:
                break

        logger.info("cleanup deleted %d old events before %s", total, cutoff.date())
        return total
    except Exception:
        logger.exception("event cleanup failed")
        db.rollback()
        return 0
    finally:
        db.close()


# ─── Scheduler 启动 / 关闭 ──────────────────────────────────────────────


def start():
    """注册定时任务并启动调度器（app startup 时调用）"""
    scheduler.add_job(
        _agg_cells, "interval", minutes=5,
        id="agg_heatmap_cells", replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        _cleanup_old_events, "cron", hour=3, minute=0,
        id="cleanup_old_events", replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info("world scheduler started: agg=5min, cleanup=daily@03:00 UTC")


def stop():
    scheduler.shutdown(wait=False)
    logger.info("world scheduler stopped")
