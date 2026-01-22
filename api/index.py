from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId
import base64
import random
import string
import io
from datetime import datetime, timedelta
from threading import Thread
import time

app = Flask(__name__)
CORS(app)

# MongoDB connection
MONGO_URL = "mongodb+srv://filmzi2120_db_user:zerodev@cluster0.stpgmbo.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URL)
db = client['filehosting']
files_collection = db['files']

# Auto-delete configuration
AUTO_DELETE_DAYS = 14  # Delete files after 2 weeks

# Generate random 4-letter code
def generate_code():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))

# Get file extension based on content type
def get_extension(content_type):
    extensions = {
        'image/jpeg': '.jpg',
        'image/png': '.png',
        'image/gif': '.gif',
        'image/webp': '.webp',
        'video/mp4': '.mp4',
        'video/mpeg': '.mpeg',
        'video/webm': '.webm',
        'application/pdf': '.pdf',
        'application/msword': '.doc',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
        'text/plain': '.txt',
    }
    return extensions.get(content_type, '.bin')

# Background task to delete old files
def cleanup_old_files():
    while True:
        try:
            # Calculate cutoff date (2 weeks ago)
            cutoff_date = datetime.utcnow() - timedelta(days=AUTO_DELETE_DAYS)
            
            # Find and delete old files
            result = files_collection.delete_many({
                'uploaded_at': {'$lt': cutoff_date}
            })
            
            if result.deleted_count > 0:
                print(f"[AUTO-DELETE] Deleted {result.deleted_count} files older than {AUTO_DELETE_DAYS} days")
            
        except Exception as e:
            print(f"[AUTO-DELETE ERROR] {str(e)}")
        
        # Run cleanup every 6 hours
        time.sleep(21600)

# Start cleanup thread
def start_cleanup_thread():
    cleanup_thread = Thread(target=cleanup_old_files, daemon=True)
    cleanup_thread.start()
    print(f"[AUTO-DELETE] Cleanup thread started - files will be deleted after {AUTO_DELETE_DAYS} days")

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'message': 'File Hosting API',
        'auto_delete': f'Files are automatically deleted after {AUTO_DELETE_DAYS} days',
        'endpoints': {
            'upload': 'POST /upload',
            'view': 'GET /{code}',
            'delete': 'DELETE /{code}',
            'list': 'GET /files',
            'info': 'GET /info/{code}'
        }
    })

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Read file data
        file_data = file.read()
        content_type = file.content_type
        extension = get_extension(content_type)
        
        # Generate unique code
        code = generate_code()
        while files_collection.find_one({'code': code}):
            code = generate_code()
        
        # Store in MongoDB
        file_doc = {
            'code': code,
            'filename': file.filename,
            'content_type': content_type,
            'extension': extension,
            'data': base64.b64encode(file_data).decode('utf-8'),
            'size': len(file_data),
            'uploaded_at': datetime.utcnow()
        }
        
        result = files_collection.insert_one(file_doc)
        
        # Get base URL from request
        base_url = request.host_url.rstrip('/')
        
        return jsonify({
            'success': True,
            'code': code,
            'url': f"{base_url}/{code}{extension}",
            'direct_link': f"{base_url}/{code}{extension}",
            'delete_url': f"{base_url}/delete/{code}",
            'id': str(result.inserted_id),
            'expires_in_days': AUTO_DELETE_DAYS
        }), 201
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/<code>', methods=['GET'])
def get_file(code):
    try:
        # Remove extension if present
        code_clean = code.split('.')[0]
        
        file_doc = files_collection.find_one({'code': code_clean})
        
        if not file_doc:
            return jsonify({'error': 'File not found'}), 404
        
        # Decode base64 data
        file_data = base64.b64decode(file_doc['data'])
        
        return send_file(
            io.BytesIO(file_data),
            mimetype=file_doc['content_type'],
            as_attachment=False,
            download_name=file_doc['filename']
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/delete/<code>', methods=['DELETE', 'GET'])
def delete_file(code):
    try:
        code_clean = code.split('.')[0]
        
        result = files_collection.delete_one({'code': code_clean})
        
        if result.deleted_count == 0:
            return jsonify({'error': 'File not found'}), 404
        
        return jsonify({
            'success': True,
            'message': 'File deleted successfully'
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/files', methods=['GET'])
def list_files():
    try:
        files = files_collection.find({}, {'data': 0})  # Exclude file data
        base_url = request.host_url.rstrip('/')
        
        file_list = []
        for file in files:
            # Calculate days until deletion
            days_old = (datetime.utcnow() - file['uploaded_at']).days
            days_remaining = AUTO_DELETE_DAYS - days_old
            
            file_list.append({
                'id': str(file['_id']),
                'code': file['code'],
                'filename': file['filename'],
                'content_type': file['content_type'],
                'size': file['size'],
                'url': f"{base_url}/{file['code']}{file['extension']}",
                'uploaded_at': file['uploaded_at'].isoformat(),
                'days_remaining': max(0, days_remaining)
            })
        
        return jsonify({
            'success': True,
            'count': len(file_list),
            'files': file_list
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/info/<code>', methods=['GET'])
def file_info(code):
    try:
        code_clean = code.split('.')[0]
        
        file_doc = files_collection.find_one({'code': code_clean}, {'data': 0})
        
        if not file_doc:
            return jsonify({'error': 'File not found'}), 404
        
        base_url = request.host_url.rstrip('/')
        
        # Calculate days until deletion
        days_old = (datetime.utcnow() - file_doc['uploaded_at']).days
        days_remaining = AUTO_DELETE_DAYS - days_old
        
        return jsonify({
            'success': True,
            'id': str(file_doc['_id']),
            'code': file_doc['code'],
            'filename': file_doc['filename'],
            'content_type': file_doc['content_type'],
            'extension': file_doc['extension'],
            'size': file_doc['size'],
            'url': f"{base_url}/{file_doc['code']}{file_doc['extension']}",
            'uploaded_at': file_doc['uploaded_at'].isoformat(),
            'days_remaining': max(0, days_remaining)
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Start the cleanup thread before running the app
    start_cleanup_thread()
    app.run(debug=True)
