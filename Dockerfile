# 自己架設（self-host）用的映像檔。用法見 DEPLOY.md，最簡單的方式是：
#   docker compose up -d --build
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 每位使用者的操作都在各自的執行緒裡建站點圖。系統預設會給每條執行緒一塊自己的
# 記憶體區，舊圖釋放後留下的空間別的執行緒用不到，佔用量就越疊越高。限制成 2 塊之後，
# 實測連續 20 次換設定的最高用量從 2.7 GB 降到 1.9 GB。
ENV MALLOC_ARENA_MAX=2

# 以一般使用者（非 root）身分執行。程式會把站點資料快取寫到 data/cache，
# 所以整個 /app 都交給這個使用者，確保寫得進去。
RUN useradd --create-home --uid 1000 app
WORKDIR /app
RUN chown app:app /app

# 先只複製相依清單並安裝：之後只改程式碼時，這一層可以直接沿用，重建會快很多。
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .
USER app

EXPOSE 8501

# 讓 Docker 知道 App 是否正常：Streamlit 內建的健康檢查端點回 200 才算健康。
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).status == 200 else 1)"

# --server.address=0.0.0.0：讓容器外連得進來（實際開放給誰，由 docker-compose.yml 的 ports 決定）。
# --browser.serverAddress=localhost：只影響啟動訊息。不設的話，Streamlit 每次啟動都會上網查
#   這台電腦的對外 IP 並印在記錄裡，還標成「External URL」，但那個網址其實連不進來。
# --client.toolbarMode=viewer：隱藏右上角給開發者用的 Deploy 按鈕與選單項目。
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--browser.serverAddress=localhost", \
     "--client.toolbarMode=viewer"]
