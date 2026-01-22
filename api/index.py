from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId
import gridfs
import os
import random
import string
from datetime import datetime
import io

app = Flask(__name__)
CORS(app)

# MongoDB connection
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)
db = client['file_hosting']
fs = gridfs.GridFS(db)
files_collection = db['files']

# File type extensions mapping
EXTENSION_MAP = {
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'video/mp4': '.mp4',
    'video/mpeg': '.mpeg',
    'video/quicktime': '.mov',
    'video/x-msvideo': '.avi',
    'video/webm': '.webm',
    'application/pdf': '.pdf',
    'application/msword': '.doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'text/plain': '.txt',
    'application/zip': '.zip',
    'application/x-rar-compressed': '.rar'
}

def generate_random_code(length=4):
    """Generate random alphanumeric code"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def get_extension(content_type):
    """Get file extension from content type"""
    return EXTENSION_MAP.get(content_type, '.bin')

@app.route('/')
def home():
    return jsonify({
        'message': 'File Hosting API',
        'endpoints': {
            'upload': 'POST /upload',
            'view': 'GET /:code',
            'delete': 'DELETE /delete/:file_id',
            'list': 'GET /files'
        }
    })

@app.route('/upload', methods=['POST'])
def upload_file():
    """Upload a file and return unique URL"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    # Get file metadata
    content_type = file.content_type or 'application/octet-stream'
    extension = get_extension(content_type)
    
    # Generate unique code
    code = generate_random_code(4)
    while files_collection.find_one({'code': code}):
        code = generate_random_code(4)
    
    # Store file in GridFS
    file_data = file.read()
    file_id = fs.put(
        file_data,
        filename=file.filename,
        content_type=content_type
    )
    
    # Store metadata
    file_doc = {
        'code': code,
        'extension': extension,
        'original_name': file.filename,
        'content_type': content_type,
        'file_id': file_id,
        'size': len(file_data),
        'uploaded_at': datetime.utcnow()
    }
    
    result = files_collection.insert_one(file_doc)
    
    # Generate URLs
    base_url = request.host_url.rstrip('/')
    file_url = f"{base_url}/{code}{extension}"
    
    return jsonify({
        'success': True,
        'file_id': str(result.inserted_id),
        'code': code,
        'url': file_url,
        'direct_link': file_url,
        'delete_url': f"{base_url}/delete/{str(result.inserted_id)}",
        'size': len(file_data),
        'type': content_type
    }), 201

@app.route('/<path:filename>')
def view_file(filename):
    """View/download file by code and extension"""
    # Extract code and extension
    parts = filename.rsplit('.', 1)
    if len(parts) == 2:
        code, ext = parts
        ext = '.' + ext
    else:
        code = filename
        ext = None
    
    # Find file metadata
    query = {'code': code}
    if ext:
        query['extension'] = ext
    
    file_doc = files_collection.find_one(query)
    
    if not file_doc:
        return jsonify({'error': 'File not found'}), 404
    
    # Retrieve file from GridFS
    try:
        grid_file = fs.get(file_doc['file_id'])
        file_data = grid_file.read()
        
        return Response(
            file_data,
            mimetype=file_doc['content_type'],
            headers={
                'Content-Disposition': f'inline; filename="{file_doc["original_name"]}"'
            }
        )
    except Exception as e:
        return jsonify({'error': 'Error retrieving file', 'details': str(e)}), 500

@app.route('/delete/<file_id>', methods=['DELETE'])
def delete_file(file_id):
    """Delete a file by its ID"""
    try:
        file_doc = files_collection.find_one({'_id': ObjectId(file_id)})
        
        if not file_doc:
            return jsonify({'error': 'File not found'}), 404
        
        # Delete from GridFS
        fs.delete(file_doc['file_id'])
        
        # Delete metadata
        files_collection.delete_one({'_id': ObjectId(file_id)})
        
        return jsonify({
            'success': True,
            'message': 'File deleted successfully'
        }), 200
        
    except Exception as e:
        return jsonify({'error': 'Error deleting file', 'details': str(e)}), 500

@app.route('/files', methods=['GET'])
def list_files():
    """List all uploaded files"""
    files = []
    for doc in files_collection.find().sort('uploaded_at', -1):
        base_url = request.host_url.rstrip('/')
        files.append({
            'file_id': str(doc['_id']),
            'code': doc['code'],
            'original_name': doc['original_name'],
            'url': f"{base_url}/{doc['code']}{doc['extension']}",
            'size': doc['size'],
            'type': doc['content_type'],
            'uploaded_at': doc['uploaded_at'].isoformat(),
            'delete_url': f"{base_url}/delete/{str(doc['_id'])}"
        })
    
    return jsonify({
        'success': True,
        'count': len(files),
        'files': files
    })

@app.route('/info/<code>', methods=['GET'])
def file_info(code):
    """Get file information by code"""
    file_doc = files_collection.find_one({'code': code})
    
    if not file_doc:
        return jsonify({'error': 'File not found'}), 404
    
    base_url = request.host_url.rstrip('/')
    
    return jsonify({
        'success': True,
        'file_id': str(file_doc['_id']),
        'code': file_doc['code'],
        'original_name': file_doc['original_name'],
        'url': f"{base_url}/{file_doc['code']}{file_doc['extension']}",
        'size': file_doc['size'],
        'type': file_doc['content_type'],
        'uploaded_at': file_doc['uploaded_at'].isoformat()
    })

# Vercel serverless function handler
def handler(request):
    with app.request_context(request.environ):
        return app.full_dispatch_request()
