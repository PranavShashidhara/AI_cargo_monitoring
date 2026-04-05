# Use official Python 3.11 slim image for a lightweight, secure base
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Set environment variables to optimize Python execution
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies required for compiling ML/Data packages
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file first to leverage Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project structure into the container
COPY . .

# Create the local storage directories in case they aren't tracked by git
RUN mkdir -p data logs/compliance_audit

# Expose port 8501 for the Streamlit Human-in-the-Loop Dashboard
EXPOSE 8501

# Add a healthcheck to ensure the container is serving the UI correctly
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Command to run the Streamlit application
CMD ["streamlit", "run", "src/main_dashboard.py", "--server.port=8501", "--server.address=0.0.0.0"]