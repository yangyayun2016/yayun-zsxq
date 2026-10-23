# 同步计划

## A. 知识星球 → GitHub

通过 zsxq-cli 每日增量把知识星球内容同步到本仓库。

## B. blog 精选 → 知识星球

`tianjikuajing/blog` 中标记了 `publish_to_zsxq` 的精选文章，反推为知识星球主题：新建用 topic `+create`，更新用 topic `+edit`。

## 去重

`mapping/sync.json` 记录已同步关系，避免同一内容重复写入。
