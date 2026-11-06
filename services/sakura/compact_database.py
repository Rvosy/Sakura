"""去掉完整报告已包含的重复字段，可选回收数据库文件空间。"""

import argparse
import json

from db import connect


def remove_duplicate_evidence(connection):
    changed = 0
    removed_bytes = 0
    cursor = connection.execute(
        "SELECT id,report_json,stack_json,breadcrumbs_json FROM error_events "
        "WHERE report_json IS NOT NULL AND (stack_json IS NOT NULL OR breadcrumbs_json IS NOT NULL) ORDER BY id"
    )
    while rows := cursor.fetchmany(128):
        with connection:
            for row_id, raw, stack, breadcrumbs in rows:
                report = json.loads(raw)
                fields = []
                for name, stored in (("stack", stack), ("breadcrumbs", breadcrumbs)):
                    if stored is not None and name in report and json.loads(stored) == report[name]:
                        fields.append(name + "_json=NULL")
                        removed_bytes += len(stored.encode("utf-8"))
                if fields:
                    connection.execute(
                        "UPDATE error_events SET " + ",".join(fields) + " WHERE id=?",
                        (row_id,),
                    )
                    changed += 1
    return {"deduplicatedRows": changed, "removedTextBytes": removed_bytes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vacuum", action="store_true", help="回收空闲页；执行期间需要暂停服务写入")
    args = parser.parse_args()
    with connect() as connection:
        # 重整大库时复用页面，减少默认小缓存造成的反复磁盘读写。
        connection.execute("PRAGMA cache_size=-32768")
        print("正在检查重复字段", flush=True)
        result = remove_duplicate_evidence(connection)
        print(json.dumps(result), flush=True)
        if args.vacuum:
            print("正在回收数据库空间", flush=True)
            connection.execute("VACUUM")
            print("数据库空间回收完成", flush=True)


if __name__ == "__main__":
    main()
