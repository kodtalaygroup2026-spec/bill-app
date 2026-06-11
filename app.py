import streamlit as st
import anthropic
import base64
import json
import re
import datetime
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# ── Setup ─────────────────────────────────────────────────────
client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

@st.cache_resource
def get_workbook():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    gc = gspread.authorize(creds)
    return gc.open("Bill App Data")

def get_sheet():
    return get_workbook().worksheet("Sheet1")

@st.cache_data(ttl=300)
def load_master_list() -> list[dict]:
    ws = get_workbook().worksheet("MasterList")
    return ws.get_all_records()

# ── Match Logic ───────────────────────────────────────────────
def normalize(text: str) -> str:
    """ลบช่องว่างและแปลงเป็นพิมพ์เล็กเพื่อเปรียบเทียบ"""
    return str(text).strip().lower().replace(" ", "")

def match_supplier_item(supplier: str, item: str, master: list[dict]) -> dict:
    """
    แมช supplier + item กับ Master List
    คืนค่า dict ที่มี supplier_standard, item_standard, match_status
    """
    sup_norm  = normalize(supplier)
    item_norm = normalize(item)

    # ── ระดับ 1: ตรงทั้ง supplier และ item ──────────────────
    for row in master:
        if (normalize(row["supplier_raw"]) == sup_norm and
                normalize(row["item_raw"]) == item_norm):
            return {
                "supplier_standard": row["supplier_standard"],
                "item_standard":     row["item_standard"],
                "category":          row.get("category", ""),
                "match_status":      "✅ ตรง"
            }

    # ── ระดับ 2: ตรง supplier + item ใกล้เคียง ──────────────
    for row in master:
        sup_match  = normalize(row["supplier_raw"]) in sup_norm or \
                     sup_norm in normalize(row["supplier_raw"])
        item_match = normalize(row["item_raw"]) in item_norm or \
                     item_norm in normalize(row["item_raw"])
        if sup_match and item_match:
            return {
                "supplier_standard": row["supplier_standard"],
                "item_standard":     row["item_standard"],
                "category":          row.get("category", ""),
                "match_status":      "🔶 ใกล้เคียง"
            }

    # ── ระดับ 3: ตรง supplier อย่างเดียว ─────────────────────
    for row in master:
        sup_match = normalize(row["supplier_raw"]) in sup_norm or \
                    sup_norm in normalize(row["supplier_raw"])
        if sup_match:
            return {
                "supplier_standard": row["supplier_standard"],
                "item_standard":     item,  # ใช้ชื่อเดิม
                "category":          row.get("category", ""),
                "match_status":      "🔶 supplier ตรง / item ไม่พบ"
            }

    # ── ระดับ 4: ไม่พบเลย ────────────────────────────────────
    return {
        "supplier_standard": supplier,
        "item_standard":     item,
        "category":          "",
        "match_status":      "❓ ไม่พบใน Master"
    }

# ── UI ────────────────────────────────────────────────────────
st.title("🧾 ระบบอ่านบิลอัตโนมัติ")
st.write("อัปโหลดรูปใบเสร็จเพื่อวิเคราะห์")

uploaded_file = st.file_uploader(
    "เลือกรูปใบเสร็จ",
    type=["jpg", "jpeg", "png"]
)

if uploaded_file is not None:
    st.image(uploaded_file, caption="รูปที่อัปโหลด", width=300)

    if st.button("🔍 วิเคราะห์บิล"):
        with st.spinner("กำลังอ่านบิล..."):

            image_data = base64.b64encode(
                uploaded_file.getvalue()
            ).decode("utf-8")

            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=2000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_data
                            }
                        },
                        {
                            "type": "text",
                            "text": """อ่านข้อมูลจากใบเสร็จ/บิลนี้ แล้วแปลงเป็น JSON array
ตัวอย่าง format:
[
  {
    "รับของ": "30/3/2026",
    "bill_date": "29/3/2026",
    "supplier": "ชื่อซัพพลายเออร์",
    "item": "ชื่อสินค้า",
    "SPEC": "",
    "QUANTITY": 1,
    "PRICE": 100.0,
    "amount": 100.0,
    "discount": 0,
    "note": ""
  }
]
ตอบเฉพาะ JSON เท่านั้น ไม่มีข้อความอื่น ไม่มี ```"""
                        }
                    ]
                }]
            )

            raw = response.content[0].text.strip()
            raw = re.sub(r"```json|```python|```", "", raw).strip()
            match = re.search(r'\[.*\]', raw, re.DOTALL)
if match:
    raw = match.group(0)

            try:
                rows   = json.loads(raw)
                df     = pd.DataFrame(rows)
                master = load_master_list()

                # ── แมช Supplier + Item ──────────────────────
                sup_std, item_std, cats, statuses = [], [], [], []

                for _, row in df.iterrows():
                    result = match_supplier_item(
                        str(row.get("supplier", "")),
                        str(row.get("item", "")),
                        master
                    )
                    sup_std.append(result["supplier_standard"])
                    item_std.append(result["item_standard"])
                    cats.append(result["category"])
                    statuses.append(result["match_status"])

                df["supplier_original"] = df["supplier"]
                df["item_original"]     = df["item"]
                df["supplier"]          = sup_std
                df["item"]              = item_std
                df["category"]          = cats
                df["match_status"]      = statuses

                # ── Flag รายการผิดปกติ ───────────────────────
                def flag_row(row):
                    issues = []
                    if pd.to_numeric(row.get("discount", 0), errors="coerce") < 0:
                        issues.append("⚠️ discount ติดลบ")
                    if pd.to_numeric(row.get("amount", 0), errors="coerce") <= 0:
                        issues.append("⚠️ amount ผิดปกติ")
                    if not str(row.get("supplier", "")).strip():
                        issues.append("⚠️ ไม่มี supplier")
                    if "ไม่พบ" in str(row.get("match_status", "")):
                        issues.append("❓ ไม่พบใน Master")
                    return ", ".join(issues) if issues else "✅ ปกติ"

                df["flag"] = df.apply(flag_row, axis=1)
                st.session_state["bill_df"] = df

            except json.JSONDecodeError:
                st.error("❌ Claude ตอบกลับมาไม่เป็น JSON — ลองอัปโหลดรูปใหม่ครับ")
                st.code(raw)

# ── Preview และส่ง ────────────────────────────────────────────
if "bill_df" in st.session_state:
    df = st.session_state["bill_df"]

    st.success("✅ วิเคราะห์และแมชข้อมูลเสร็จแล้ว!")

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("ยอดรวม",
                f"{pd.to_numeric(df['amount'], errors='coerce').sum():,.2f} บาท")
    col2.metric("จำนวนรายการ", len(df))
    col3.metric("แมชสำเร็จ",
                len(df[df["match_status"] == "✅ ตรง"]))
    col4.metric("ต้องตรวจสอบ",
                len(df[df["flag"] != "✅ ปกติ"]))

    # ── ตารางผลการแมช ────────────────────────────────────────
    st.subheader("🔍 ผลการแมช Supplier + Item")
    st.dataframe(
        df[["supplier_original", "supplier",
            "item_original", "item",
            "category", "match_status"]],
        use_container_width=True
    )

    # Flag warning
    warnings = df[df["flag"] != "✅ ปกติ"]
    if not warnings.empty:
        st.warning(f"⚠️ พบ {len(warnings)} รายการต้องตรวจสอบ")
        st.dataframe(
            warnings[["supplier", "item", "amount", "discount", "flag"]],
            use_container_width=True
        )

    # Editable table
    st.subheader("📊 ตรวจสอบและแก้ไขข้อมูล")
    edited_df = st.data_editor(
        df, use_container_width=True, num_rows="dynamic"
    )

    # Summary per supplier
    st.subheader("📈 ยอดรวมต่อ Supplier")
    summary = (
        edited_df.groupby("supplier")
        .agg(รายการ=("item", "count"),
             ยอดรวม=("amount", "sum"))
        .reset_index()
        .sort_values("ยอดรวม", ascending=False)
    )
    st.dataframe(summary, use_container_width=True)

    # ── ส่ง Google Sheets ─────────────────────────────────────
    st.subheader("📤 ส่งข้อมูลไป Google Sheets")

    po_number = st.text_input("เลข PO", placeholder="เช่น PO-2026-0001")
    status    = st.selectbox("สถานะ",
                             ["pending", "approved", "paid", "rejected"])

    if st.button("✅ ยืนยันและส่งไป Google Sheets", type="primary"):
        if not po_number.strip():
            st.warning("⚠️ กรุณากรอกเลข PO ก่อนส่งครับ")
        else:
            try:
                with st.spinner("กำลังส่งข้อมูล..."):
                    sheet     = get_sheet()
                    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

                    send_df = edited_df.copy()
                    send_df["PO_number"]    = po_number.strip()
                    send_df["วันที่บันทึก"] = timestamp
                    send_df["สถานะ"]        = status

                    existing = sheet.get_all_values()
                    if len(existing) == 0:
                        sheet.append_row(send_df.columns.tolist())

                    sheet.append_rows(
                        send_df.values.tolist(),
                        value_input_option="USER_ENTERED"
                    )

                st.success("🎉 ส่งสำเร็จแล้วครับ!")
                st.balloons()

                st.subheader("📋 สรุปที่ส่งไป")
                st.dataframe(
                    send_df[["supplier", "item", "category",
                              "amount", "PO_number",
                              "วันที่บันทึก", "สถานะ"]],
                    use_container_width=True
                )

            except Exception as e:
                st.error(f"❌ ส่งไม่สำเร็จ: {e}")
