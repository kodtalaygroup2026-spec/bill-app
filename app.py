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
def get_sheet():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    gc = gspread.authorize(creds)
    return gc.open("Bill App Data").worksheet("Sheet1")  # ← เปลี่ยนชื่อ Sheet ให้ตรง

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
            raw = re.sub(r"```json|```", "", raw).strip()

            try:
                rows = json.loads(raw)
                df = pd.DataFrame(rows)

                # ── Flag รายการผิดปกติ ──────────────────────
                def flag_row(row):
                    issues = []
                    if pd.to_numeric(row.get("discount", 0), errors="coerce") < 0:
                        issues.append("⚠️ discount ติดลบ")
                    if pd.to_numeric(row.get("amount", 0), errors="coerce") <= 0:
                        issues.append("⚠️ amount ผิดปกติ")
                    if not str(row.get("supplier", "")).strip():
                        issues.append("⚠️ ไม่มี supplier")
                    return ", ".join(issues) if issues else "✅ ปกติ"

                df["flag"] = df.apply(flag_row, axis=1)
                st.session_state["bill_df"] = df

            except json.JSONDecodeError:
                st.error("❌ Claude ตอบกลับมาไม่เป็น JSON — ลองอัปโหลดรูปใหม่ครับ")
                st.code(raw)

# ── Preview และส่ง ────────────────────────────────────────────
if "bill_df" in st.session_state:
    df = st.session_state["bill_df"]

    st.success("✅ วิเคราะห์เสร็จแล้ว!")

    # Summary metrics
    col1, col2, col3 = st.columns(3)
    col1.metric("ยอดรวม", f"{pd.to_numeric(df['amount'], errors='coerce').sum():,.2f} บาท")
    col2.metric("จำนวนรายการ", len(df))
    col3.metric("รายการผิดปกติ", len(df[df["flag"] != "✅ ปกติ"]))

    # Flag warning
    warnings = df[df["flag"] != "✅ ปกติ"]
    if not warnings.empty:
        st.warning(f"⚠️ พบ {len(warnings)} รายการผิดปกติ กรุณาตรวจสอบก่อนส่ง")
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

    status = st.selectbox(
        "สถานะ",
        ["pending", "approved", "paid", "rejected"]
    )

    if st.button("✅ ยืนยันและส่งไป Google Sheets", type="primary"):
        if not po_number.strip():
            st.warning("⚠️ กรุณากรอกเลข PO ก่อนส่งครับ")
        else:
            try:
                with st.spinner("กำลังส่งข้อมูล..."):
                    sheet = get_sheet()
                    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

                    send_df = edited_df.copy()
                    send_df["PO_number"]    = po_number.strip()
                    send_df["วันที่บันทึก"] = timestamp
                    send_df["สถานะ"]        = status

                    # เพิ่ม header ถ้า sheet ว่าง
                    existing = sheet.get_all_values()
                    if len(existing) == 0:
                        sheet.append_row(send_df.columns.tolist())

                    sheet.append_rows(
                        send_df.values.tolist(),
                        value_input_option="USER_ENTERED"
                    )

                st.success("🎉 ส่งสำเร็จแล้วครับ!")
                st.balloons()

                # แสดงสรุปที่ส่งไป
                st.subheader("📋 สรุปที่ส่งไป")
                st.dataframe(
                    send_df[["supplier", "item", "amount",
                              "PO_number", "วันที่บันทึก", "สถานะ"]],
                    use_container_width=True
                )

            except Exception as e:
                st.error(f"❌ ส่งไม่สำเร็จ: {e}")
