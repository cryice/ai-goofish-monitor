# 闲鱼监控系统功能更新日志

## 更新日期
2026-05-25

---

## 1. 起始页功能

### 功能说明
支持从指定页码开始爬取，实现断点续爬功能。

### 数据库变更
```sql
ALTER TABLE tasks ADD COLUMN start_page INTEGER NOT NULL DEFAULT 1;
```

### 后端修改

| 文件 | 修改内容 |
|------|----------|
| [sqlite_connection.py](src/infrastructure/persistence/sqlite_connection.py#L34) | 添加 `start_page` 列 |
| [sqlite_task_repository.py](src/infrastructure/persistence/sqlite_task_repository.py#L92-L96) | INSERT/REPLACE SQL 包含 start_page |
| [sqlite_bootstrap.py](src/infrastructure/persistence/sqlite_bootstrap.py#L80-L95) | 旧数据迁移支持 start_page |
| [task.py](src/domain/models/task.py#L118-L120) | Task/TaskCreate/TaskUpdate/TaskGenerateRequest 模型添加字段 |

### 前端修改

| 文件 | 修改内容 |
|------|----------|
| [TaskForm.vue](web-ui/src/components/tasks/TaskForm.vue#L337-L340) | 前端表单添加起始页输入框 |
| [zh-CN-extra.ts](web-ui/src/i18n/messages/zh-CN-extra.ts#L67) | 中文翻译：`startPage: '起始页数'` |
| [en-US-extra.ts](web-ui/src/i18n/messages/en-US-extra.ts#L67) | 英文翻译：`startPage: 'Start Page'` |

---

## 2. 页码跳转功能

### 功能说明
支持通过页码输入框直接跳转到指定页码，提高爬取效率。

### 后端修改

| 文件 | 修改内容 |
|------|----------|
| [search_pagination.py](src/services/search_pagination.py#L53-L117) | 新增 `jump_to_page()` 函数，支持页码输入框跳转 |
| [search_pagination.py](src/services/search_pagination.py#L15-L16) | 更新选择器匹配闲鱼实际分页器：<br>`PAGE_INPUT_SELECTOR = "input[class*='search-pagination-to-page-input']"`<br>`GO_BUTTON_SELECTOR = "button[class*='search-pagination-to-page-confirm-button']"` |
| [scraper.py](src/scraper.py#L60-L64) | 导入 `jump_to_page` 函数 |
| [scraper.py](src/scraper.py#L925-L948) | 启动时检测 `start_page > 1` 并跳转 |

---

## 3. 翻页逻辑修复

### 问题描述
原逻辑 `range(start_page, max_pages + 1)` 导致起始页 > 搜索页数时循环为空。

### 修复方案
修改循环范围为 `range(start_page, start_page + max_pages)`

| 配置示例 | 爬取页码 |
|----------|----------|
| 起始页=4, 搜索页数=3 | 4, 5, 6 |
| 起始页=1, 搜索页数=5 | 1, 2, 3, 4, 5 |

### 修改文件
| 文件 | 修改内容 |
|------|----------|
| [scraper.py](src/scraper.py#L949-L952) | 修复循环范围 |

---

## 4. SKU数据抓取

### 功能说明
自动点击购买/APP下单按钮，提取商品的所有 SKU 名称和价格。

### 后端修改

| 文件 | 修改内容 |
|------|----------|
| [scraper.py](src/scraper.py#L107-L188) | 新增 `_fetch_sku_data()` 函数 |
| [scraper.py](src/scraper.py#L1154-L1157) | 在详情页处理中调用 SKU 获取 |
| [scraper.py](src/scraper.py#L116-L127) | 支持多种按钮文本：购买、立即购买、APP下单 |

### 按钮选择器
```python
buy_button_selectors = [
    "text=购买",
    "text=立即购买",
    "text=APP下单",
    "button:has-text('购买')",
    "button:has-text('APP下单')",
    "a:has-text('购买')",
    "a:has-text('APP下单')"
]
```

---

## 5. SKU数据存储

### 数据库变更
```sql
CREATE TABLE IF NOT EXISTS item_skus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_item_id INTEGER NOT NULL,
    sku_name TEXT NOT NULL,
    sku_price TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (result_item_id) REFERENCES result_items(id) ON DELETE CASCADE
);
```

### 后端修改

| 文件 | 修改内容 |
|------|----------|
| [sqlite_connection.py](src/infrastructure/persistence/sqlite_connection.py#L102-L112) | 创建 `item_skus` 表 |
| [result_storage_service.py](src/services/result_storage_service.py#L168-L206) | 保存商品时同时保存 SKU 数据 |

---

## 6. SKU查询API

### API端点
```
GET /api/results/{filename}/item/{item_id}/skus
```

### 功能说明
根据商品ID查询该商品的所有SKU数据。

### 响应示例
```json
{
  "skus": [
    {
      "id": 1,
      "sku_name": "原色钛金属 256G",
      "sku_price": "6999",
      "created_at": "2026-05-25 23:30:00"
    },
    {
      "id": 2,
      "sku_name": "蓝色钛金属 512G",
      "sku_price": "7999",
      "created_at": "2026-05-25 23:30:00"
    }
  ]
}
```

### 修改文件
| 文件 | 修改内容 |
|------|----------|
| [results.py](src/api/routes/results.py#L30-L32) | 导入依赖 |
| [results.py](src/api/routes/results.py#L229-L277) | 新增 SKU 查询 API |

---

## 7. 二次AI分析

### 功能说明
支持对已爬取的商品进行二次 AI 分析，可自定义分析提示词。

### API端点
```
POST /api/results/{filename}/reanalyze
```

### 请求参数
```json
{
  "item_ids": ["item_id_1", "item_id_2"],
  "custom_prompt": "自定义分析提示词（可选）"
}
```

### 响应示例
```json
{
  "results": [
    {
      "item_id": "item_id_1",
      "success": true,
      "is_recommended": true,
      "reason": "商品描述清晰，价格合理"
    },
    {
      "item_id": "item_id_2",
      "success": false,
      "error": "AI 分析失败"
    }
  ]
}
```

### 修改文件
| 文件 | 修改内容 |
|------|----------|
| [results.py](src/api/routes/results.py#L33-L34) | 导入 AI 服务 |
| [results.py](src/api/routes/results.py#L283-L364) | 新增二次分析 API |

---

## 新增API汇总

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/results/{filename}/item/{item_id}/skus` | GET | 获取指定商品的 SKU 列表 |
| `/api/results/{filename}/reanalyze` | POST | 对指定商品进行二次 AI 分析 |

---

## 数据库变更汇总

```sql
-- 1. 添加起始页字段
ALTER TABLE tasks ADD COLUMN start_page INTEGER NOT NULL DEFAULT 1;

-- 2. 创建SKU表
CREATE TABLE IF NOT EXISTS item_skus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_item_id INTEGER NOT NULL,
    sku_name TEXT NOT NULL,
    sku_price TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (result_item_id) REFERENCES result_items(id) ON DELETE CASCADE
);
```

---

## 使用说明

### 起始页功能
1. 在任务表单中设置"起始页数"（默认1）
2. 设置"搜索页数"为需要爬取的页数
3. 任务会从起始页开始爬取指定页数的数据

### SKU数据
- 系统自动在访问商品详情页时抓取SKU数据
- SKU数据会自动存储到数据库
- 可通过API查询指定商品的SKU列表

### 二次分析
1. 在结果页面选择需要重新分析的商品
2. 调用二次分析API
3. 可自定义分析提示词，或使用原始提示词
4. 分析结果会更新到数据库

---

## 注意事项

1. **起始页功能**：起始页从1开始，最大页码由搜索页数决定
2. **SKU抓取**：部分商品可能没有SKU数据，系统会自动跳过
3. **二次分析**：需要配置有效的AI服务（OpenAI兼容接口）
4. **数据库迁移**：首次运行时会自动添加新字段和表