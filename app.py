import streamlit as st
import anthropic
import base64
from PIL import Image
import io

# ตั้งค่า API Key
client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

# หน้าตา App
st.title("🧾 ระบบอ่านบิลอัตโนมัติ")
st.write("อัปโหลดรูปใบเสร็จเพื่อวิเคราะห์")

# อัปโหลดรูป
uploaded_file = st.file_uploader(
    "เลือกรูปใบเสร็จ", 
    type=["jpg", "jpeg", "png"]
)

if uploaded_file is not None:
    # แสดงรูปที่อัปโหลด
    st.image(uploaded_file, caption="รูปที่อัปโหลด", width=300)
    
    # กดปุ่มวิเคราะห์
    if st.button("🔍 วิเคราะห์บิล"):
        with st.spinner("กำลังอ่านบิล..."):
            
            # แปลงรูปเป็น base64
            image_data = base64.b64encode(
                uploaded_file.getvalue()
            ).decode("utf-8")
            
            # ส่งให้ Claude วิเคราะห์
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=1000,
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
                            "text": """วิเคราะห์ใบเสร็จนี้และสรุปข้อมูลดังนี้
                            1. ชื่อร้าน
                            2. วันที่
                            3. รายการสินค้าและราคา
                            4. ยอดรวม
                            ตอบเป็นภาษาไทย"""
                        }
                    ]
                }]
            )
            
            # แสดงผล
            st.success("✅ วิเคราะห์เสร็จแล้ว!")
            st.write(response.content[0].text)