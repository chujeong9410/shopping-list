"""쇼핑리스트 앱 (Streamlit). 실행: streamlit run app.py"""

import json
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import streamlit as st

DATA_FILE = Path(__file__).parent / "shopping_list.json"
MAX_NAME_LENGTH = 50
MAX_QUANTITY = 999
DOUBLE_SUBMIT_WINDOW = 1.5  # 초. 추가 직후 이 시간 안에 들어온 빈 제출은 더블클릭으로 보고 무시한다


class LoadError(Exception):
    """저장 파일을 읽을 수 없음 (권한, 잠김 등). 이 상태에서는 저장하지 않는다."""


class SaveError(Exception):
    """저장 파일에 쓸 수 없음."""


# ──────────────────────────────────────────────
# 데이터 처리
# 데이터의 기준은 항상 파일이다. 매 렌더링과 매 변경마다 파일을 다시 읽어서,
# 여러 탭에서 동시에 써도 다른 탭의 변경을 덮어쓰지 않는다.
# ──────────────────────────────────────────────
def _validate_item(raw) -> dict | None:
    """저장 파일의 아이템 하나를 검사한다. 형식이 틀리면 None을 반환한다."""
    if not isinstance(raw, dict):
        return None
    item_id, name = raw.get("id"), raw.get("name")
    quantity, checked = raw.get("quantity"), raw.get("checked")
    if not isinstance(item_id, str) or not item_id:
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        return None
    if not isinstance(checked, bool):
        return None
    created_at = raw.get("created_at")
    return {
        "id": item_id,
        "name": _clean_name(name),
        "quantity": quantity,
        "checked": checked,
        "created_at": created_at if isinstance(created_at, str) else "",
    }


def _backup_corrupt_file() -> Path | None:
    """손상된 저장 파일을 옆에 보관하고 보관 경로를 반환한다. 실패하면 None."""
    backup = DATA_FILE.with_name(
        f"shopping_list.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json"
    )
    try:
        os.replace(DATA_FILE, backup)
        return backup
    except OSError:
        return None


def load_items() -> tuple[list[dict], str | None]:
    """저장 파일에서 아이템 목록을 불러온다.

    Returns:
        (아이템 리스트, 경고 메시지). 파일이 손상되었으면 원본을 백업하고,
        읽을 수 있는 아이템만 남긴 리스트와 경고 메시지를 반환한다.

    Raises:
        LoadError: 권한 문제 등으로 파일 자체를 읽을 수 없을 때.
    """
    if not DATA_FILE.exists():
        return [], None
    try:
        # utf-8-sig: 메모장이나 PowerShell로 편집해 BOM이 붙은 파일도 읽는다
        text = DATA_FILE.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = None
    except OSError as e:
        raise LoadError(f"저장 파일을 읽을 수 없습니다({e.strerror}).") from e

    try:
        raw = json.loads(text) if text is not None else None
    except json.JSONDecodeError:
        raw = None

    items: list[dict] = []
    if isinstance(raw, list):
        seen: set[str] = set()
        for entry in raw:
            item = _validate_item(entry)
            if item and item["id"] not in seen:
                seen.add(item["id"])
                items.append(item)
        if len(items) == len(raw):
            return items, None

    # 손상됨: 원본을 보관한 뒤 정상 아이템만 다시 저장한다
    backup = _backup_corrupt_file()
    if backup is None:
        raise LoadError("저장 파일이 손상되었고, 원본을 백업하지 못해 열지 않았습니다.")
    try:
        save_items(items)
    except SaveError:
        pass
    kept = f"읽을 수 있는 {len(items)}개 항목만 불러왔습니다" if items else "빈 리스트로 시작합니다"
    return items, f"저장 파일이 손상되어 {kept}. 원본은 `{backup.name}`에 보관했습니다."


def save_items(items: list[dict]) -> None:
    """아이템 목록을 저장 파일에 기록한다. 임시 파일에 먼저 쓴 뒤 교체한다.

    Raises:
        SaveError: 파일에 쓸 수 없을 때.
    """
    tmp = DATA_FILE.with_name(DATA_FILE.name + ".tmp")
    try:
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, DATA_FILE)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise SaveError(f"저장하지 못했습니다({e.strerror}).") from e


def _clean_name(name: str) -> str:
    """앞뒤 공백을 제거하고 최대 길이로 자른다."""
    return (name or "").strip()[:MAX_NAME_LENGTH]


def _valid_quantity(quantity) -> int | None:
    """수량이 1~MAX_QUANTITY 범위의 정수면 int로, 아니면 None을 반환한다."""
    try:
        value = int(quantity)
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= MAX_QUANTITY else None


def _find_item(items: list[dict], item_id: str) -> dict | None:
    return next((item for item in items if item["id"] == item_id), None)


def find_duplicate(items: list[dict], name: str) -> dict | None:
    """대소문자와 앞뒤 공백을 무시하고 같은 이름의 아이템을 찾는다."""
    key = _clean_name(name).casefold()
    return next((item for item in items if item["name"].casefold() == key), None)


def add_item(items: list[dict], name: str, quantity: int = 1) -> dict | None:
    """새 아이템을 추가한다. 이름이 비었거나 수량이 범위를 벗어나면 None을 반환한다."""
    name, quantity = _clean_name(name), _valid_quantity(quantity)
    if not name or quantity is None:
        return None
    item = {
        "id": str(uuid.uuid4()),
        "name": name,
        "quantity": quantity,
        "checked": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    items.append(item)
    return item


def update_item(items: list[dict], item_id: str, name: str, quantity: int) -> bool:
    """id에 해당하는 아이템의 이름과 수량을 수정한다. 성공하면 True를 반환한다."""
    name, quantity = _clean_name(name), _valid_quantity(quantity)
    item = _find_item(items, item_id)
    if not name or quantity is None or item is None:
        return False
    item["name"] = name
    item["quantity"] = quantity
    return True


def delete_item(items: list[dict], item_id: str) -> tuple[int, dict] | None:
    """id에 해당하는 아이템을 삭제하고 (삭제 위치, 아이템)을 반환한다."""
    for index, item in enumerate(items):
        if item["id"] == item_id:
            del items[index]
            return index, item
    return None


def delete_checked_items(items: list[dict]) -> list[tuple[int, dict]]:
    """체크된 아이템을 모두 삭제한다.

    Returns:
        앞에서부터 하나씩 지웠을 때의 (삭제 위치, 아이템) 목록. 역순으로 되돌리면 원래 순서가 된다.
    """
    removed = []
    kept = []
    for item in items:
        if item["checked"]:
            removed.append((len(kept), item))
        else:
            kept.append(item)
    items[:] = kept
    return removed


def restore_items(items: list[dict], entries: list[tuple[int, dict]]) -> int:
    """삭제했던 아이템을 원래 위치에 되돌리고, 되돌린 개수를 반환한다."""
    existing = {item["id"] for item in items}
    restored = 0
    for index, item in reversed(entries):
        if item["id"] not in existing:
            items.insert(min(index, len(items)), item)
            existing.add(item["id"])
            restored += 1
    return restored


def toggle_item(items: list[dict], item_id: str, checked: bool | None = None) -> None:
    """id에 해당하는 아이템의 체크 상태를 바꾼다. checked를 주면 그 값으로, 없으면 반전한다."""
    item = _find_item(items, item_id)
    if item is not None:
        item["checked"] = (not item["checked"]) if checked is None else checked


# ──────────────────────────────────────────────
# 상태 관리
# ──────────────────────────────────────────────
def init_state() -> None:
    defaults = {
        "shopping_items": [],
        "load_warning": None,
        "load_error": None,
        "save_error": None,
        "editing_id": None,
        "edit_error": None,
        "add_error": None,
        "undo_entries": [],
        "pending_duplicate": None,
        "last_add_at": 0.0,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def refresh_items() -> list[dict]:
    """파일에서 최신 목록을 읽어 세션에 반영한다. 읽기 실패 시 직전 목록을 유지한다."""
    try:
        items, warning = load_items()
    except LoadError as e:
        st.session_state.load_error = str(e)
        return st.session_state.shopping_items
    st.session_state.load_error = None
    if warning:
        st.session_state.load_warning = warning
    st.session_state.shopping_items = items
    return items


def apply_change(change: Callable[[list[dict]], object]):
    """최신 파일 내용에 change를 적용하고 저장한다. 실패하면 아무것도 바꾸지 않는다.

    Returns:
        change의 반환값. 읽기나 저장에 실패하면 None.
    """
    try:
        items, warning = load_items()
    except LoadError as e:
        st.session_state.save_error = f"{e} 변경 사항을 저장하지 않았습니다."
        return None
    if warning:
        st.session_state.load_warning = warning
    result = change(items)
    try:
        save_items(items)
    except SaveError as e:
        st.session_state.save_error = f"{e} 파일이 읽기 전용이거나 다른 프로그램이 사용 중인지 확인해 주세요."
        return None
    st.session_state.save_error = None
    st.session_state.load_warning = None  # 정상 저장되었으므로 손상 경고는 더 이상 필요 없다
    st.session_state.shopping_items = items
    return result


# ──────────────────────────────────────────────
# 콜백 (상태 변경은 모두 여기서 처리)
# ──────────────────────────────────────────────
def _notify(message: str) -> None:
    """다음 렌더링 때 st.toast로 보여줄 메시지를 예약한다."""
    st.session_state.pending_toast = message


def _clear_transient() -> None:
    """삭제 외의 동작이 일어나면 되돌리기와 중복 안내를 닫는다."""
    st.session_state.undo_entries = []
    st.session_state.pending_duplicate = None


def _add_and_notify(name: str, quantity: int) -> None:
    item = apply_change(lambda items: add_item(items, name, quantity))
    if item:
        st.session_state.last_add_at = time.monotonic()
        cut = " (50자까지만 저장됨)" if len(name.strip()) > MAX_NAME_LENGTH else ""
        _notify(f"🛒 {md_escape(item['name'])} 추가됨{cut}")


def on_add() -> None:
    name = st.session_state.get("new_name", "") or ""
    quantity = st.session_state.get("new_qty")
    st.session_state.add_error = None
    if not name.strip():
        # 추가 직후 바로 들어온 빈 제출은 더블클릭으로 보고 조용히 무시한다
        if time.monotonic() - st.session_state.last_add_at > DOUBLE_SUBMIT_WINDOW:
            st.session_state.add_error = "아이템명을 입력해 주세요."
        return
    if _valid_quantity(quantity) is None:
        st.session_state.add_error = f"수량은 1~{MAX_QUANTITY} 사이의 정수로 입력해 주세요."
        st.session_state.new_name_restore = name  # 폼이 비워지므로 다음 렌더링에서 되살린다
        return
    _clear_transient()
    duplicate = find_duplicate(refresh_items(), name)
    if duplicate:
        st.session_state.pending_duplicate = {
            "id": duplicate["id"], "name": _clean_name(name), "quantity": int(quantity),
        }
        return
    _add_and_notify(name, int(quantity))


def on_duplicate_merge() -> None:
    pending = st.session_state.pending_duplicate
    st.session_state.pending_duplicate = None
    if not pending:
        return

    def merge(items):
        item = _find_item(items, pending["id"])
        if item is None:
            return add_item(items, pending["name"], pending["quantity"])
        item["quantity"] = min(item["quantity"] + pending["quantity"], MAX_QUANTITY)
        item["checked"] = False  # 다시 사야 하므로 미구매로 되돌린다
        return item

    item = apply_change(merge)
    if item:
        _notify(f"➕ {md_escape(item['name'])} 수량 {item['quantity']}개로 변경됨")


def on_duplicate_add_new() -> None:
    pending = st.session_state.pending_duplicate
    st.session_state.pending_duplicate = None
    if pending:
        _add_and_notify(pending["name"], pending["quantity"])


def on_duplicate_cancel() -> None:
    st.session_state.pending_duplicate = None


def on_toggle(item_id: str) -> None:
    _clear_transient()
    checked = bool(st.session_state.get(f"check_{item_id}"))
    apply_change(lambda items: toggle_item(items, item_id, checked))


def _clear_edit_state() -> None:
    item_id = st.session_state.editing_id
    if item_id:
        st.session_state.pop(f"name_{item_id}", None)
        st.session_state.pop(f"qty_{item_id}", None)
    st.session_state.editing_id = None
    st.session_state.edit_error = None


def on_edit_start(item_id: str) -> None:
    _clear_transient()
    _clear_edit_state()  # 다른 아이템을 편집 중이었다면 그 변경은 버린다
    item = _find_item(refresh_items(), item_id)
    if item is None:
        return
    st.session_state.editing_id = item_id
    st.session_state[f"name_{item_id}"] = item["name"]
    st.session_state[f"qty_{item_id}"] = item["quantity"]


def on_edit_save(item_id: str) -> None:
    name = st.session_state.get(f"name_{item_id}", "") or ""
    quantity = st.session_state.get(f"qty_{item_id}")
    if not name.strip():
        st.session_state.edit_error = "아이템명을 입력해 주세요."
        return
    if _valid_quantity(quantity) is None:
        st.session_state.edit_error = f"수량은 1~{MAX_QUANTITY} 사이의 정수로 입력해 주세요."
        return
    if _find_item(refresh_items(), item_id) is None:
        _clear_edit_state()
        st.session_state.save_error = "다른 곳에서 이미 삭제된 항목이라 저장하지 않았습니다."
        return
    ok = apply_change(lambda items: update_item(items, item_id, name, quantity))
    if ok:
        _clear_edit_state()
        item = _find_item(st.session_state.shopping_items, item_id)
        _notify(f"✏️ {md_escape(item['name'])} 수정됨")


def on_edit_cancel() -> None:
    _clear_edit_state()


def on_delete(item_id: str) -> None:
    st.session_state.pending_duplicate = None
    if st.session_state.editing_id == item_id:
        _clear_edit_state()
    removed = apply_change(lambda items: delete_item(items, item_id))
    if removed:
        # 연속으로 지운 항목은 한 번에 되돌릴 수 있도록 쌓아 둔다
        st.session_state.undo_entries = st.session_state.undo_entries + [removed]


def on_delete_checked() -> None:
    st.session_state.pending_duplicate = None
    if st.session_state.editing_id:
        editing = _find_item(st.session_state.shopping_items, st.session_state.editing_id)
        if editing and editing["checked"]:
            _clear_edit_state()
    removed = apply_change(delete_checked_items)
    if removed:
        st.session_state.undo_entries = st.session_state.undo_entries + removed


def on_undo() -> None:
    entries = st.session_state.undo_entries
    st.session_state.undo_entries = []
    restored = apply_change(lambda items: restore_items(items, entries))
    if restored:
        _notify(f"↩️ {restored}개 항목을 되돌렸습니다")


def on_undo_dismiss() -> None:
    st.session_state.undo_entries = []


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|~<>:$&])")
_WORD_JOINER = "⁠"  # 보이지 않고 줄바꿈도 일으키지 않는 문자

CUSTOM_CSS = """
<style>
/* 좁은 화면(모바일)에서도 입력 줄과 아이템 행의 열이 세로로 쌓이지 않게 */
.st-key-add_row [data-testid="stHorizontalBlock"],
[class*="st-key-row_"] [data-testid="stHorizontalBlock"],
.st-key-undo_bar [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap;
}
.st-key-add_row [data-testid="stColumn"],
[class*="st-key-row_"] [data-testid="stColumn"],
.st-key-undo_bar [data-testid="stColumn"] {
    min-width: 0 !important;
}
/* 버튼 글자가 좁은 폭에서 줄바꿈되거나 잘리지 않게 */
.st-key-add_row button p,
[class*="st-key-row_"] button p,
.st-key-undo_bar button p { white-space: nowrap; }
/* 아이템 행을 조금 더 촘촘하게 */
[class*="st-key-row_"] { padding: 0.35rem 0.75rem; }
/* 긴 이름이 말줄임으로 잘려 수량이 가려지지 않도록 여러 줄로 표시 */
[class*="st-key-check_"] [data-testid="stMarkdownContainer"],
[class*="st-key-check_"] [data-testid="stMarkdownContainer"] p {
    white-space: normal !important;
    overflow: visible !important;
    text-overflow: clip !important;
    overflow-wrap: anywhere;
}
/* 아이콘 버튼의 글자(스크린 리더용 이름)는 화면에서만 숨긴다 */
[class*="st-key-edit_"] button [data-testid="stMarkdownContainer"],
[class*="st-key-del_"] button [data-testid="stMarkdownContainer"] {
    position: absolute !important;
    width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
}
</style>
"""


def md_escape(text: str) -> str:
    """사용자 입력을 마크다운 서식이나 자동 링크 없이 글자 그대로 보이도록 바꾼다."""
    # URL, www., 이메일 자동 링크는 이스케이프로 막히지 않으므로 보이지 않는 문자를 끼워 끊는다
    j = _WORD_JOINER
    text = text.replace("://", f"{j}://").replace("www.", f"www{j}.").replace("@", f"@{j}")
    return _MD_SPECIAL.sub(r"\\\1", text)


def render_add_form() -> None:
    restore = st.session_state.pop("new_name_restore", None)
    if restore is not None:
        st.session_state.new_name = restore
    with st.form("add_form", clear_on_submit=True, border=False):
        with st.container(key="add_row"):
            col_name, col_qty, col_btn = st.columns([5, 2.5, 2], vertical_alignment="bottom")
            col_name.text_input(
                "아이템명",
                key="new_name",
                placeholder="예: 우유",
                help=f"{MAX_NAME_LENGTH}자까지 저장됩니다.",
            )
            col_qty.number_input("수량", key="new_qty", value=1, step=1)
            col_btn.form_submit_button("추가", type="primary", on_click=on_add, width="stretch")
    if st.session_state.add_error:
        st.warning(st.session_state.add_error, icon="⚠️")
        st.session_state.add_error = None


def render_duplicate_prompt() -> None:
    pending = st.session_state.pending_duplicate
    if not pending:
        return
    existing = _find_item(st.session_state.shopping_items, pending["id"])
    if existing is None:
        st.session_state.pending_duplicate = None
        return
    after = min(existing["quantity"] + pending["quantity"], MAX_QUANTITY)
    with st.container(border=True, key="dup_bar"):
        st.markdown(f"**{md_escape(existing['name'])}**이(가) 이미 리스트에 있습니다.")
        col_merge, col_new, col_cancel = st.columns([4, 3, 2])
        col_merge.button(
            f"수량 더하기 ({existing['quantity']} → {after})", type="primary",
            width="stretch", on_click=on_duplicate_merge,
        )
        col_new.button("따로 추가", width="stretch", on_click=on_duplicate_add_new)
        col_cancel.button("취소", width="stretch", on_click=on_duplicate_cancel)


def render_undo_bar() -> None:
    entries = st.session_state.undo_entries
    if not entries:
        return
    if len(entries) == 1:
        message = f"🗑️ **{md_escape(entries[0][1]['name'])}** 삭제됨"
    else:
        message = f"🗑️ **{len(entries)}개** 항목 삭제됨"
    with st.container(border=True, key="undo_bar"):
        col_msg, col_undo, col_close = st.columns([6, 3, 1], vertical_alignment="center")
        col_msg.markdown(message)
        col_undo.button("되돌리기", icon=":material/undo:", width="stretch", on_click=on_undo)
        col_close.button("✕", key="undo_close", help="닫기", type="tertiary", on_click=on_undo_dismiss)


def item_label(item: dict) -> str:
    """체크박스 라벨. 체크된 아이템은 취소선 + 회색으로 표시한다."""
    text = f"{md_escape(item['name'])} ({item['quantity']})"
    return f":gray[~~{text}~~]" if item["checked"] else text


def render_view_row(item: dict) -> None:
    item_id = item["id"]
    name = md_escape(item["name"])
    # 파일이 기준이므로 위젯 상태를 매번 파일 값에 맞춘다 (다른 탭에서 바뀐 경우 포함)
    st.session_state[f"check_{item_id}"] = item["checked"]
    with st.container(border=True, key=f"row_{item_id}"):
        col_check, col_edit, col_del = st.columns([8, 1, 1], vertical_alignment="center")
        col_check.checkbox(
            item_label(item), key=f"check_{item_id}", on_change=on_toggle, args=(item_id,),
        )
        col_edit.button(
            f"{name} 수정", icon="✏️", key=f"edit_{item_id}", help="수정", type="tertiary",
            on_click=on_edit_start, args=(item_id,),
        )
        col_del.button(
            f"{name} 삭제", icon="🗑️", key=f"del_{item_id}", help="삭제", type="tertiary",
            on_click=on_delete, args=(item_id,),
        )


def render_edit_row(item: dict) -> None:
    item_id = item["id"]
    with st.container(border=True, key=f"row_{item_id}"):
        # 폼으로 감싸서 이름 칸에서 Enter를 누르면 저장되게 한다
        with st.form(f"edit_form_{item_id}", border=False):
            col_name, col_qty = st.columns([7, 3], vertical_alignment="bottom")
            col_name.text_input(
                "아이템명", key=f"name_{item_id}", help=f"{MAX_NAME_LENGTH}자까지 저장됩니다.",
            )
            col_qty.number_input("수량", key=f"qty_{item_id}", step=1)
            if st.session_state.edit_error:
                st.warning(st.session_state.edit_error, icon="⚠️")
            col_save, col_cancel, _ = st.columns([3, 3, 4])
            col_save.form_submit_button(
                "저장", type="primary", width="stretch",
                on_click=on_edit_save, args=(item_id,),
            )
            col_cancel.form_submit_button("취소", width="stretch", on_click=on_edit_cancel)


def main() -> None:
    st.set_page_config(page_title="쇼핑리스트", page_icon="🛒", layout="centered")
    init_state()
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    items = refresh_items()

    # 편집 중이던 항목이 다른 탭에서 삭제되었으면 편집 모드를 닫는다
    if st.session_state.editing_id and _find_item(items, st.session_state.editing_id) is None:
        _clear_edit_state()

    if st.session_state.get("pending_toast"):
        st.toast(st.session_state.pop("pending_toast"))

    st.title("🛒 쇼핑리스트")
    st.caption("살 것을 기록하고, 산 것은 체크하세요")

    if st.session_state.load_error:
        st.error(st.session_state.load_error, icon="🚫")
    if st.session_state.load_warning:
        st.warning(st.session_state.load_warning, icon="⚠️")
    if st.session_state.save_error:
        st.error(st.session_state.save_error, icon="🚫")
        st.session_state.save_error = None

    render_add_form()
    render_duplicate_prompt()
    render_undo_bar()

    if not items:
        st.info("쇼핑리스트가 비어 있습니다. 위에서 살 것을 추가해 보세요.", icon="🧺")
        return

    done = sum(1 for item in items if item["checked"])
    total = len(items)
    col_progress, col_sort = st.columns([7, 3], vertical_alignment="bottom")
    col_progress.progress(done / total, text=f"**{done} / {total}** 구매 완료")
    sort_checked_last = col_sort.toggle(
        "산 것은 아래로", value=True, key="sort_checked_last",
        help="끄면 체크해도 항목이 제자리에 남습니다.",
    )
    if done == total:
        st.success("장보기 완료!", icon="🎉")

    # 미체크 항목을 위에, 체크된 항목을 아래에 (각 그룹 안에서는 추가한 순서 유지)
    ordered = sorted(items, key=lambda i: i["checked"]) if sort_checked_last else items
    for item in ordered:
        if st.session_state.editing_id == item["id"]:
            render_edit_row(item)
        else:
            render_view_row(item)

    if done:
        st.button(
            "체크된 항목 모두 삭제", icon=":material/delete_sweep:",
            on_click=on_delete_checked,
        )


main()
