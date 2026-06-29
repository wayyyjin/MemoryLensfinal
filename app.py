import streamlit as st
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from datetime import datetime
from geopy.geocoders import Nominatim
from openai import OpenAI
import base64
import io
import math
import time



st.set_page_config(page_title="Memory Lens", page_icon="📸", layout="wide")

APP_NAME = "Memory Lens"
MODEL_NAME = "gpt-4o-mini"

SHORT_TIME_GAP_MINUTES = 5
ACTIVITY_TIME_GAP_MINUTES = 180
DISTANCE_GAP_METERS = 150
ACTIVITY_DISTANCE_GAP_METERS = 500


st.title("📸 Memory Lens")
st.subheader("사진으로 하루를 기억해주는 나만의 AI Agent")
st.write("---")


def get_openai_api_key():
    try:
        return st.secrets["OPENAI_API_KEY"]
    except Exception:
        return None


api_key = get_openai_api_key()

if not api_key:
    st.error("OpenAI API Key가 설정되지 않았습니다.")
    st.info(
        """
        프로젝트 폴더 안에 아래 파일을 만들어 주세요.

        .streamlit/secrets.toml

        그리고 secrets.toml 안에 이렇게 입력하세요.

        OPENAI_API_KEY = "너의_API_KEY"
        """
    )
    st.stop()


client = OpenAI(api_key=api_key)

user_name = st.text_input(
    "사용자 이름을 입력하세요",
    value="홍길동",
    placeholder="예: 홍길동"
)

if not user_name.strip():
    user_name = "사용자"

uploaded_files = st.file_uploader(
    "사진을 여러 장 업로드하세요.",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True
)

user_memo = st.text_area(
    "오늘 전체에 대해 남기고 싶은 메모가 있다면 적어주세요.\nex) 오늘 새롭게 깨달았거나 배운 점",
    height=100
)

geolocator = Nominatim(user_agent="memory_lens_photo_diary_agent")


def get_exif(image):
    try:
        raw = image._getexif()
        if not raw:
            return {}
        return {TAGS.get(k, k): v for k, v in raw.items()}
    except Exception:
        return {}


def get_photo_time(exif):
    for key in ["DateTimeOriginal", "DateTimeDigitized", "DateTime"]:
        if key in exif:
            try:
                return datetime.strptime(exif[key], "%Y:%m:%d %H:%M:%S")
            except Exception:
                pass
    return None


def convert_to_degrees(value):
    d, m, s = value
    return float(d) + float(m) / 60 + float(s) / 3600


def get_gps_info(exif):
    gps_data = exif.get("GPSInfo")
    if not gps_data:
        return None

    gps = {GPSTAGS.get(k, k): v for k, v in gps_data.items()}

    try:
        lat = convert_to_degrees(gps["GPSLatitude"])
        lon = convert_to_degrees(gps["GPSLongitude"])

        if gps.get("GPSLatitudeRef") != "N":
            lat = -lat
        if gps.get("GPSLongitudeRef") != "E":
            lon = -lon

        return {"lat": lat, "lon": lon}
    except Exception:
        return None


def clean_address(address):
    if not address:
        return "위치 정보 없음"

    parts = [p.strip() for p in address.split(",") if p.strip()]
    keep = []

    for p in parts:
        if any(x in p for x in [
            "특별자치도", "특별시", "광역시",
            "시", "군", "구", "동", "읍", "면", "리",
            "항", "해변", "시장", "거리", "카페", "공원", "역", "대학교", "식당"
        ]):
            keep.append(p)

    keep = list(dict.fromkeys(keep))
    return " ".join(keep[:6]) if keep else address


@st.cache_data(show_spinner=False)
def gps_to_address(lat, lon):
    try:
        location = geolocator.reverse(
            (lat, lon),
            language="ko",
            exactly_one=True,
            timeout=10
        )
        time.sleep(1)

        if location:
            return clean_address(location.address)
    except Exception:
        pass

    return f"위도 {lat:.6f}, 경도 {lon:.6f}"


def format_korean_time(dt):
    if not dt:
        return "촬영 시간 없음"

    ampm = "오전" if dt.hour < 12 else "오후"
    hour = dt.hour % 12 or 12

    return f"{ampm} {hour}시 {dt.minute:02d}분"


def haversine_meters(gps1, gps2):
    if not gps1 or not gps2:
        return None

    r = 6371000
    lat1 = math.radians(gps1["lat"])
    lat2 = math.radians(gps2["lat"])
    dlat = math.radians(gps2["lat"] - gps1["lat"])
    dlon = math.radians(gps2["lon"] - gps1["lon"])

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return r * c


def image_to_base64(image):
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def make_thumbnail(image, max_width=360):
    img = image.copy()
    img.thumbnail((max_width, max_width))
    return img


def load_photos(files):
    photos = []

    for file in files:
        image = Image.open(file)
        exif = get_exif(image)
        photo_time = get_photo_time(exif)
        gps = get_gps_info(exif)

        if gps:
            address = gps_to_address(gps["lat"], gps["lon"])
        else:
            address = "위치 정보 없음"

        photos.append({
            "file_name": file.name,
            "image": image.copy(),
            "time": photo_time,
            "time_text": format_korean_time(photo_time),
            "gps": gps,
            "address": address,
            "event_note": ""
        })

    photos.sort(key=lambda x: x["time"] or datetime.max)
    return photos


def same_place(photo, last_photo, long_activity=False):
    distance = haversine_meters(photo["gps"], last_photo["gps"])

    if distance is None:
        if photo["address"] != "위치 정보 없음" and photo["address"] == last_photo["address"]:
            return True
        return True

    limit = ACTIVITY_DISTANCE_GAP_METERS if long_activity else DISTANCE_GAP_METERS
    return distance <= limit


def group_photos(photos):
    groups = []

    for photo in photos:
        if not groups:
            groups.append([photo])
            continue

        last_group = groups[-1]
        last_photo = last_group[-1]

        if not photo["time"] or not last_photo["time"]:
            groups.append([photo])
            continue

        diff_minutes = abs((photo["time"] - last_photo["time"]).total_seconds()) / 60

        same_short_event = (
            diff_minutes <= SHORT_TIME_GAP_MINUTES
            and same_place(photo, last_photo, long_activity=False)
        )

        same_long_activity = (
            diff_minutes <= ACTIVITY_TIME_GAP_MINUTES
            and same_place(photo, last_photo, long_activity=True)
        )

        if same_short_event or same_long_activity:
            last_group.append(photo)
        else:
            groups.append([photo])

    return groups


def group_time_text(group):
    start = group[0]["time"]
    end = group[-1]["time"]

    if not start:
        return "촬영 시간 없음"

    if start == end or not end:
        return format_korean_time(start)

    return f"{format_korean_time(start)} ~ {format_korean_time(end)}"


def group_address(group):
    for photo in group:
        if photo["address"] != "위치 정보 없음":
            return photo["address"]
    return "위치 정보 없음"


def add_event_notes_ui(groups):
    st.write("## 📝 이벤트별 메모")
    st.caption(
        "AI가 상황을 더 잘 이해하도록 힌트를 적어주세요. "
        "이 내용은 최종 결과에 그대로 들어가지 않고, GPT가 추론할 때만 참고합니다."
    )

    for idx, group in enumerate(groups, start=1):
        with st.expander(
            f"Event {idx} | {group_time_text(group)} | {group_address(group)} | 사진 {len(group)}장",
            expanded=False
        ):
            cols = st.columns(min(len(group), 6))

            for i, photo in enumerate(group):
                with cols[i % len(cols)]:
                    st.image(
                        make_thumbnail(photo["image"], 260),
                        caption=photo["time_text"],
                        width="content"
                    )

            event_note = st.text_area(
                f"Event {idx} 메모",
                placeholder="예: 친구들과 낚시하러 방파제에 도착함. 시작할 때와 끝날 때만 사진을 찍음.",
                key=f"event_note_{idx}",
                height=100
            )

            for photo in group:
                photo["event_note"] = event_note.strip()

    return groups


def make_event_note_text(group):
    note = group[0].get("event_note", "")

    if not note:
        return "사용자 이벤트 메모 없음"

    return note


def analyze_event(client, group, event_index, user_name):
    time_text = group_time_text(group)
    address = group_address(group)
    event_note_text = make_event_note_text(group)

    content = [{
        "type": "input_text",
        "text": f"""
너는 사진 기반 하루 기록 AI Agent야.

이 사진들은 하나의 시간대 또는 같은 활동 흐름에서 촬영된 사진들이야.
사진을 각각 따로 설명하지 말고, 하나의 활동 또는 이벤트로 묶어서 자연스럽게 기록해줘.

사용자 이름: {user_name}
이벤트 번호: {event_index}
시간: {time_text}
위치: {address}
사진 수: {len(group)}장

사용자 이벤트 메모:
{event_note_text}

출력 형식:
시간: {time_text}
위치: {address}
기록:

작성 규칙:
- 반드시 위 출력 형식 그대로 써.
- 사용자 이벤트 메모는 사진보다 더 중요한 맥락 정보로 참고해.
- 단, 사용자 이벤트 메모를 결과에 그대로 복사하지 마.
- 메모는 추론을 돕는 clue로만 사용하고, 최종 기록은 자연스러운 일기 문장으로 다시 작성해.
- 사진 속 인물을 무조건 사용자로 단정하지 마. 다만 사용자가 메모에서 본인, 가족, 친구, 동료 등 관계를 알려준 경우에는 그 정보를 활용해도 돼.
- 위치는 단순 주소가 아니라, 사진 속 간판, 메뉴판, 음식, 풍경, 물건, 사용자 메모 등을 보고 특정 식당/카페/장소명을 유추할 수 있으면 자세히 써줘.
- 여러 사진을 종합해서 카페, 음식, 산책, 쇼핑, 바다, 이동, 작업, 공부, 식사, 낚시, 운동, 축구, 야외활동 등을 추측해.
- 만약 사진 간 시간이 1~3시간 정도 차이 나더라도 같은 장소와 같은 활동으로 보이면, 그 시간 동안 활동이 이어진 것으로 자연스럽게 서술해.
- 확실하지 않은 내용은 "~로 보여요.", "~하신 것 같아요.", "~했을 가능성이 있어요."라고 써.
- 메모에 없는 사람의 이름, 신원, 개인정보는 새로 만들어내지 마.
- 좌표는 쓰지 마.
- 5문장 이상으로 작성해.
"""
    }]

    for photo in group[:6]:
        b64 = image_to_base64(photo["image"])
        content.append({
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{b64}"
        })

    response = client.responses.create(
        model=MODEL_NAME,
        input=[{"role": "user", "content": content}]
    )

    return response.output_text.strip()


def summarize_day(client, event_records, memo, user_name):
    joined = "\n\n".join(event_records)

    prompt = f"""
너는 사용자의 하루를 정리하는 AI Life Logger야.

사용자 이름: {user_name}

사용자 메모:
{memo if memo.strip() else "없음"}

이벤트별 기록:
{joined}

작성 규칙:
- 반드시 "{user_name}님은 오늘"로 시작해.
- 시간 흐름에 따라 하루를 자연스럽게 연결해.
- 위치 이동, 카페, 식사, 산책, 쇼핑, 풍경 감상, 낚시, 운동 같은 활동을 포함해.
- 사용자가 직접 남긴 전체 메모는 참고하되, 그대로 복사하지 말고 자연스럽게 반영해.
- 사진 사이 시간이 길어도 같은 활동으로 이어진 경우에는 하나의 흐름으로 설명해.
- 확실하지 않은 내용은 추측처럼 표현해.
- 1년 뒤에 읽어도 이날 무엇을 했는지 떠올릴 수 있게 써.
- 6~10문장으로 작성해.
"""

    response = client.responses.create(
        model=MODEL_NAME,
        input=prompt
    )

    return response.output_text.strip()


if uploaded_files:
    with st.spinner("사진의 시간·위치 정보를 읽는 중입니다..."):
        photos = load_photos(uploaded_files)

    with st.spinner("사진을 이벤트로 묶는 중입니다..."):
        groups = group_photos(photos)

    st.success(f"총 {len(photos)}장의 사진을 {len(groups)}개의 이벤트로 묶었습니다.")

    groups = add_event_notes_ui(groups)

    st.write("## 📌 이벤트 미리보기")

    for idx, group in enumerate(groups, start=1):
        with st.expander(
            f"Event {idx} | {group_time_text(group)} | {group_address(group)} | 사진 {len(group)}장"
        ):
            cols = st.columns(min(len(group), 6))

            for i, photo in enumerate(group):
                with cols[i % len(cols)]:
                    st.image(
                        make_thumbnail(photo["image"], 280),
                        caption=photo["time_text"],
                        width="content"
                    )

    st.write("---")

    if st.button("🚀 AI 사진 일기 생성"):
        event_records = []

        progress = st.progress(0)
        status = st.empty()

        for idx, group in enumerate(groups, start=1):
            status.write(f"Event {idx}/{len(groups)} 분석 중...")
            record = analyze_event(client, group, idx, user_name)
            event_records.append(record)
            progress.progress(idx / len(groups))

        status.write("하루 전체 요약 생성 중...")
        day_summary = summarize_day(client, event_records, user_memo, user_name)

        st.write("# 📷 사진 일기")

        for idx, group in enumerate(groups, start=1):
            st.write(f"## Event {idx}")

            cols = st.columns(min(len(group), 6))

            for i, photo in enumerate(group):
                with cols[i % len(cols)]:
                    st.image(
                        make_thumbnail(photo["image"], 300),
                        caption=photo["time_text"],
                        width="content"
                    )

            st.markdown(event_records[idx - 1])
            st.write("---")

        st.write("# 📖 AI 하루 요약")
        st.info(day_summary)

        diary_text = "# 사진별 이벤트 기록\n\n"
        diary_text += "\n\n".join(event_records)
        diary_text += "\n\n# AI 하루 요약\n\n"
        diary_text += day_summary

        st.download_button(
            label="📄 TXT로 다운로드",
            data=diary_text,
            file_name="memory_lens_diary.txt",
            mime="text/plain"
        )

else:
    st.info("사진을 업로드하면 AI가 하루 기록을 만들어줍니다.")

