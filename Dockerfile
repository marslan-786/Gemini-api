# Official Playwright image (Includes Python + Browsers)
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Work directory set karein
WORKDIR /app

# Requirements copy karein aur install karein
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Browsers install karein (Ahtiyat ke tor par)
RUN playwright install chromium
RUN playwright install-deps

# Sara code copy karein
COPY . .

# Port expose karein
EXPOSE 8080

# App chalayein
CMD ["python", "main.py"]
