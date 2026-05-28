"""
结果文件管理路由
"""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from enum import Enum

from pydantic import BaseModel
from urllib.parse import quote
from contextlib import contextmanager
import json
from datetime import datetime

from src.infrastructure.persistence.sqlite_connection import sqlite_connection
from src.services.price_history_service import build_price_history_insights
from src.services.result_export_service import build_results_csv
from src.services.result_file_service import (
    enrich_records_with_price_insight,
    validate_result_filename,
)
from src.services.result_storage_service import (
    build_result_ndjson,
    delete_result_file_records,
    list_result_filenames,
    load_all_result_records,
    load_result_blacklist_keywords,
    load_visible_result_item_ids,
    query_result_records,
    result_file_exists,
    save_result_blacklist_keywords,
    update_item_status,
)
from src.infrastructure.persistence.storage_names import build_result_filename
from src.services.ai_service import AIAnalysisService
from src.infrastructure.external.ai_client import AIClient


router = APIRouter(prefix="/api/results", tags=["results"])

DEFAULT_EXPORT_FILENAME = "export.csv"


def _sku_lookup_keys(item_id: str | None) -> tuple[str, ...]:
    raw = str(item_id or "").strip()
    if not raw:
        return tuple()
    keys = [raw]
    if not raw.startswith("item:"):
        keys.append(f"item:{raw}")
    return tuple(dict.fromkeys(keys))


def _normalize_sku_rows(rows) -> list[dict]:
    return [
        {
            "sku_name": row["sku_name"],
            "sku_price": row["sku_price"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _attach_skus_from_conn(conn, item_data: dict, result_item_id: int) -> None:
    sku_rows = conn.execute(
        """
        SELECT sku_name, sku_price, created_at
        FROM item_skus
        WHERE result_item_id = ?
        ORDER BY id
        """,
        (result_item_id,),
    ).fetchall()

    if sku_rows:
        item_data["SKU列表"] = _normalize_sku_rows(sku_rows)
        return

    # 兼容尚未迁移到 item_skus 表、但 raw_json 中已经带 SKU 的历史记录。
    product_info = item_data.get("商品信息", {}) or {}
    raw_skus = item_data.get("SKU列表") or product_info.get("SKU列表") or []
    if raw_skus:
        item_data["SKU列表"] = raw_skus


def _build_download_headers(export_name: str) -> dict[str, str]:
    ascii_name = export_name.encode("ascii", "ignore").decode("ascii")
    if ascii_name != export_name or not ascii_name:
        ascii_name = DEFAULT_EXPORT_FILENAME
    encoded_name = quote(export_name, safe="")
    return {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{encoded_name}"
        )
    }


@router.get("/files")
async def get_result_files():
    """获取所有结果文件列表"""
    return {"files": await list_result_filenames()}


@router.get("/files/{filename:path}")
async def download_result_file(filename: str):
    """下载指定的结果文件"""
    if ".." in filename or filename.startswith("/"):
        return {"error": "非法的文件路径"}
    if not filename.endswith(".jsonl") or not await result_file_exists(filename):
        return {"error": "文件不存在"}
    return Response(
        content=await build_result_ndjson(filename),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/all-data")
async def get_all_collected_data(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    keyword: str = Query(None),
    status: str = Query(None)  # recommended, not_recommended, pending
):
    """获取所有采集的数据，支持分页、关键词筛选和状态筛选"""
    try:
        with sqlite_connection() as conn:
            # 构建查询条件
            where_conditions = []
            params = []
            
            # 添加关键词筛选
            if keyword:
                where_conditions.append("(raw_json LIKE ? OR result_filename LIKE ?)")
                params.extend([f"%{keyword}%", f"%{keyword}%"])
            
            # 添加状态筛选
            if status:
                if status == "recommended":
                    where_conditions.append("is_recommended = 1")
                elif status == "not_recommended":
                    where_conditions.append("is_recommended = 0")
                elif status == "pending":
                    where_conditions.append("is_recommended IS NULL")
            
            where_clause = ""
            if where_conditions:
                where_clause = "WHERE " + " AND ".join(where_conditions)
            
            # 查询总数
            count_query = f"""
                SELECT COUNT(*) as total
                FROM result_items
                {where_clause}
            """
            total_row = conn.execute(count_query, params).fetchone()
            total_items = total_row["total"] if total_row else 0
            
            # 查询数据
            offset = (page - 1) * limit
            query = f"""
                SELECT id, raw_json, result_filename, crawl_time as created_at
                FROM result_items
                {where_clause}
                ORDER BY crawl_time DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            
            rows = conn.execute(query, params).fetchall()
            
            # 解析JSON数据
            items = []
            for row in rows:
                try:
                    item_data = json.loads(row["raw_json"])
                    item_data["_meta"] = {
                        "filename": row["result_filename"],
                        "created_at": row["created_at"]
                    }

                    _attach_skus_from_conn(conn, item_data, row["id"])
                    
                    items.append(item_data)
                except json.JSONDecodeError:
                    continue  # 跳过无法解析的JSON
            
            total_pages = (total_items + limit - 1) // limit
            
            return {
                "total_items": total_items,
                "page": page,
                "limit": limit,
                "total_pages": total_pages,
                "items": items
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取所有采集数据时出错: {exc}")


@router.delete("/files/{filename:path}")
async def delete_result_file(filename: str):
    """删除指定的结果文件"""
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(status_code=400, detail="非法的文件路径")
    if not filename.endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="只能删除 .jsonl 文件")
    
    try:
        await delete_result_file_records(filename)
        return {"message": "删除成功", "deleted_file": filename}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@router.get("/{filename}")
async def get_result_file_content(
    filename: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    recommended_only: bool = Query(False),  # 兼容旧参数，等价于 ai_recommended_only
    ai_recommended_only: bool = Query(False),
    keyword_recommended_only: bool = Query(False),
    include_hidden: bool = Query(False),
    sort_by: str = Query("crawl_time"),
    sort_order: str = Query("desc"),
):
    """读取指定的 .jsonl 文件内容，支持分页、筛选和排序"""
    if ai_recommended_only and keyword_recommended_only:
        raise HTTPException(status_code=400, detail="AI推荐筛选与关键词推荐筛选不能同时开启。")

    if recommended_only and not ai_recommended_only and not keyword_recommended_only:
        ai_recommended_only = True

    try:
        validate_result_filename(filename)
        total_items, items = await query_result_records(
            filename,
            ai_recommended_only=ai_recommended_only,
            keyword_recommended_only=keyword_recommended_only,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            limit=limit,
            include_hidden=include_hidden,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取结果文件时出错: {exc}")
    if total_items <= 0 and not await result_file_exists(filename):
        raise HTTPException(status_code=404, detail="结果文件未找到")
    paginated_results = enrich_records_with_price_insight(items, filename)

    return {
        "total_items": total_items,
        "page": page,
        "limit": limit,
        "items": paginated_results
    }


@router.get("/{filename}/insights")
async def get_result_file_insights(filename: str):
    try:
        validate_result_filename(filename)
        keyword = filename.replace("_full_data.jsonl", "")
        visible_item_ids = load_visible_result_item_ids(filename)
        return build_price_history_insights(keyword, visible_item_ids=visible_item_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{filename}/export")
async def export_result_file_content(
    filename: str,
    recommended_only: bool = Query(False),
    ai_recommended_only: bool = Query(False),
    keyword_recommended_only: bool = Query(False),
    include_hidden: bool = Query(False),
    sort_by: str = Query("crawl_time"),
    sort_order: str = Query("desc"),
):
    if ai_recommended_only and keyword_recommended_only:
        raise HTTPException(status_code=400, detail="AI推荐筛选与关键词推荐筛选不能同时开启。")
    if recommended_only and not ai_recommended_only and not keyword_recommended_only:
        ai_recommended_only = True

    try:
        validate_result_filename(filename)
        results = await load_all_result_records(
            filename,
            ai_recommended_only=ai_recommended_only,
            keyword_recommended_only=keyword_recommended_only,
            sort_by=sort_by,
            sort_order=sort_order,
            include_hidden=include_hidden,
        )
        csv_text = build_results_csv(
            enrich_records_with_price_insight(results, filename)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"导出结果文件时出错: {exc}")
    if not results and not await result_file_exists(filename):
        raise HTTPException(status_code=404, detail="结果文件未找到")

    export_name = filename.replace(".jsonl", ".csv")
    headers = _build_download_headers(export_name)
    return Response(content=csv_text, media_type="text/csv; charset=utf-8", headers=headers)


class ItemStatus(str, Enum):
    ACTIVE = "active"
    HIDDEN = "hidden"
    EXPIRED = "expired"


class UpdateStatusRequest(BaseModel):
    status: ItemStatus


class BlacklistRulesRequest(BaseModel):
    keywords: list[str]


@router.patch("/{filename}/items/{item_id}/status")
async def patch_item_status(filename: str, item_id: str, body: UpdateStatusRequest):
    """更新指定商品的状态（active/hidden/expired）"""
    try:
        validate_result_filename(filename)
        updated = await update_item_status(filename, item_id, body.status.value)
        if not updated:
            raise HTTPException(status_code=404, detail="商品未找到")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": "状态已更新", "status": body.status.value}


@router.get("/{filename}/blacklist-rules")
async def get_result_blacklist_rules(filename: str):
    try:
        validate_result_filename(filename)
        keywords = await load_result_blacklist_keywords(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"keywords": keywords}


@router.put("/{filename}/blacklist-rules")
async def put_result_blacklist_rules(filename: str, body: BlacklistRulesRequest):
    try:
        validate_result_filename(filename)
        keywords = await save_result_blacklist_keywords(filename, body.keywords)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": "黑名单规则已更新", "keywords": keywords}


@router.get("/{filename}/item/{item_id}/skus")
async def get_item_skus(filename: str, item_id: str):
    """获取指定商品的 SKU 列表"""
    try:
        validate_result_filename(filename)
        with sqlite_connection() as conn:
            lookup_keys = _sku_lookup_keys(item_id)
            if not lookup_keys:
                raise HTTPException(status_code=404, detail="商品未找到")

            placeholders = ",".join("?" for _ in lookup_keys)
            row = conn.execute(
                f"""
                SELECT id FROM result_items
                WHERE result_filename = ?
                  AND (item_id IN ({placeholders}) OR link_unique_key IN ({placeholders}))
                LIMIT 1
                """,
                (filename, *lookup_keys, *lookup_keys),
            ).fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail="商品未找到")
            
            result_item_id = row["id"]
            
            rows = conn.execute(
                """
                SELECT id, sku_name, sku_price, created_at
                FROM item_skus
                WHERE result_item_id = ?
                ORDER BY id
                """,
                (result_item_id,)
            ).fetchall()
        skus = [
            {
                "id": row["id"],
                "sku_name": row["sku_name"],
                "sku_price": row["sku_price"],
                "created_at": row["created_at"]
            }
            for row in rows
        ]
        return {"skus": skus}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"查询 SKU 数据时出错: {exc}")


class ReanalyzeRequest(BaseModel):
    item_ids: list[str]
    custom_prompt: str = ""


@router.post("/{filename}/reanalyze")
async def reanalyze_items(filename: str, body: ReanalyzeRequest):
    """对指定商品进行二次 AI 分析"""
    try:
        validate_result_filename(filename)
        
        ai_client = AIClient()
        ai_service = AIAnalysisService(ai_client)
        
        if not ai_client.is_available():
            raise HTTPException(status_code=503, detail="AI 服务不可用")
        
        results = []
        for item_id in body.item_ids:
            with sqlite_connection() as conn:
                row = conn.execute(
                    """
                    SELECT id, raw_json FROM result_items
                    WHERE link_unique_key = ? AND result_filename = ?
                    LIMIT 1
                    """,
                    (item_id, filename)
                ).fetchone()
                
                if not row:
                    continue
                
                record = json.loads(row["raw_json"])
                product_data = record.get("商品信息", {})
                image_urls = product_data.get("商品图片列表", [])
                
                prompt_text = body.custom_prompt
                if not prompt_text:
                    prompt_text = record.get("ai_analysis", {}).get("prompt_text", "")
                
                analysis_result = await ai_service.analyze_product(
                    product_data=product_data,
                    image_paths=image_urls,
                    prompt_text=prompt_text
                )
                
                if analysis_result:
                    record["ai_analysis"] = analysis_result
                    record["ai_analysis"]["reanalyzed_at"] = datetime.now().isoformat()
                    
                    conn.execute(
                        """
                        UPDATE result_items
                        SET raw_json = ?, is_recommended = ?, analysis_source = ?
                        WHERE id = ?
                        """,
                        (
                            json.dumps(record, ensure_ascii=False),
                            1 if analysis_result.get("is_recommended") else 0,
                            "ai_reanalyze",
                            row["id"]
                        )
                    )
                    conn.commit()
                    
                    results.append({
                        "item_id": item_id,
                        "success": True,
                        "is_recommended": analysis_result.get("is_recommended"),
                        "reason": analysis_result.get("reason", "")
                    })
                else:
                    results.append({
                        "item_id": item_id,
                        "success": False,
                        "error": "AI 分析失败"
                    })
        
        return {"results": results}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"二次分析时出错: {exc}")


@router.get("/all-data")
async def get_all_collected_data(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    keyword: str = Query(None),
    status: str = Query(None),  # recommended, not_recommended, pending
    category: str = Query(None)  # 商品分类筛选
):
    """获取所有采集的数据，支持分页、关键词筛选、状态筛选和商品分类筛选"""
    try:
        with sqlite_connection() as conn:
            # 构建查询条件
            where_conditions = []
            params = []
            
            # 添加关键词筛选
            if keyword:
                where_conditions.append("(raw_json LIKE ? OR result_filename LIKE ?)")
                params.extend([f"%{keyword}%", f"%{keyword}%"])
            
            # 添加状态筛选
            if status:
                if status == "recommended":
                    where_conditions.append("is_recommended = 1")
                elif status == "not_recommended":
                    where_conditions.append("is_recommended = 0")
                elif status == "pending":
                    where_conditions.append("is_recommended IS NULL")
            
            # 添加分类筛选
            if category:
                where_conditions.append("raw_json LIKE ?")
                params.append(f'%"{category}"%')
            
            where_clause = ""
            if where_conditions:
                where_clause = "WHERE " + " AND ".join(where_conditions)
            
            # 查询总数
            count_query = f"""
                SELECT COUNT(*) as total
                FROM result_items
                {where_clause}
            """
            total_row = conn.execute(count_query, params).fetchone()
            total_items = total_row["total"] if total_row else 0
            
            # 查询数据
            offset = (page - 1) * limit
            query = f"""
                SELECT id, raw_json, result_filename, created_at
                FROM result_items
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            
            rows = conn.execute(query, params).fetchall()
            
            # 解析JSON数据
            items = []
            for row in rows:
                try:
                    item_data = json.loads(row["raw_json"])
                    item_data["_meta"] = {
                        "filename": row["result_filename"],
                        "created_at": row["created_at"]
                    }

                    _attach_skus_from_conn(conn, item_data, row["id"])
                    
                    # 根据商品特点自动分类
                    product_info = item_data.get("商品信息", {})
                    title = product_info.get("商品标题", "")
                    description = product_info.get("商品描述", "") if product_info.get("商品描述") else ""
                    full_text = f"{title} {description}".lower()
                    
                    # 定义分类规则
                    categories = []
                    if any(keyword in full_text for keyword in ["代充", "充值", "代订", "官方订阅", "会员", "升级"]):
                        categories.append("充值服务")
                    if any(keyword in full_text for keyword in ["成品号", "独立号", "账号", "成品", "独立"]):
                        categories.append("成品号")
                    if any(keyword in full_text for keyword in ["订阅", "服务", "售后", "客服", "保障"]):
                        categories.append("订阅服务")
                    if any(keyword in full_text for keyword in ["教程", "安装", "搭建", "教学", "指导", "使用方法"]):
                        categories.append("安装教程")
                    if any(keyword in full_text for keyword in ["镜像", "站点", "网站", "平台"]):
                        categories.append("镜像站")
                    if any(keyword in full_text for keyword in ["共享", "多人", "共用"]):
                        categories.append("共享账号")
                    if any(keyword in full_text for keyword in ["求购", "收购", "收", "想要"]):
                        categories.append("求购信息")
                    
                    # 如果没有匹配到任何分类，默认为"其他"
                    if not categories:
                        categories.append("其他")
                    
                    item_data["商品分类"] = categories
                    
                    items.append(item_data)
                except json.JSONDecodeError:
                    continue  # 跳过无法解析的JSON
            
            total_pages = (total_items + limit - 1) // limit
            
            return {
                "total_items": total_items,
                "page": page,
                "limit": limit,
                "total_pages": total_pages,
                "items": items
            }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"获取所有采集数据时出错: {exc}")
