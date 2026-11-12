FROM python:3.11-slim

WORKDIR /srv/site
COPY serve_site.py .
COPY html ./html

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/index.html', timeout=3).status==200 else 1)"

# 示例公告站：标准库服务器；遇到 _*.unavailable 标记的详情返回 503
CMD ["python", "serve_site.py", "--bind", "0.0.0.0", "--port", "8080", "--directory", "html"]
