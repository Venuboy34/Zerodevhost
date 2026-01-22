from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import os
import random
import string
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Initialize MongoDB connection lazily
_db = None
_fs = None
_files_collection = None

def get_db():
    """Lazy load MongoDB connection"""
    global _db, _fs, _files_collection
    
    if _db is None:
        try:
            from pymongo import MongoClient
            import gridfs
            
            MONGO_URI = os.environ.get('MONGO_URI')
            if not MONGO_URI:
                return None, None, None
            
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
            _db = client['file_hosting']
            _fs = gridfs.GridFS(_db)
            _files_collection = _db['files']
            
            # Test connection
            client.server_info()
        except Exception as e:
            print(f"MongoDB Error: {e}")
            return None, None, None
    
    return _db, _fs, _files_collection

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
    db, fs, files_collection = get_db()
    
    return jsonify({
        'message': 'API is running',
        'status': 'online',
        'database': 'connected' if db else 'disconnected',
        'version': '1.0.0'
    })

@app.route('/health')
def health():
    db, fs, files_collection = get_db()
    
    mongo_uri_set = bool(os.environ.get('MONGO_URI'))
    
    status = {
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'mongo_uri_set': mongo_uri_set,
        'database': 'connected' if db else 'disconnected'
    }
    
    if not mongo_uri_set:
        status['error'] = 'MONGO_URI environment variable not set'
        return jsonify(status), 500
    
    if not db:
        status['error'] = 'Database connection failed'
        return jsonify(status), 500
    
    return jsonify(status)

@app.route('/upload', methods=['POST'])
def upload_file():
    """Upload a file and return unique URL"""
    db, fs, files_collection = get_db()
    
    if not db:
        return jsonify({
            'error': 'Database not connected',
            'message': 'Please set MONGO_URI environment variable'
        }), 500
    
    try:
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
        attempts = 0
        while files_collection.find_one({'code': code}) and attempts < 10:
            code = generate_random_code(4)
            attempts += 1
        
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
    
    except Exception as e:
        return jsonify({
            'error': 'Upload failed',
            'message': str(e)
        }), 500

@app.route('/<path:filename>')
def view_file(filename):
    """View/download file by code and extension"""
    # Skip special routes
    if filename in ['upload', 'health', 'files']:
        return jsonify({'error': 'Invalid route'}), 404
    
    db, fs, files_collection = get_db()
    
    if not db:
        return jsonify({'error': 'Database not connected'}), 500
    
    try:
        from bson import ObjectId
        
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
        grid_file = fs.get(file_doc['file_id'])
        file_data = grid_file.read()
        
        return Response(
            file_data,
            mimetype=file_doc['content_type'],
            headers={
                'Content-Disposition': f'inline; filename="{file_doc["original_name"]}"',
                'Cache-Control': 'public, max-age=31536000'
            }
        )
    
    except Exception as e:
        return jsonify({
            'error': 'Error retrieving file',
            'message': str(e)
        }), 500

@app.route('/delete/<file_id>', methods=['DELETE', 'POST'])
def delete_file(file_id):
    """Delete a file by its ID"""
    db, fs, files_collection = get_db()
    
    if not db:
        return jsonify({'error': 'Database not connected'}), 500
    
    try:
        from bson import ObjectId
        
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
        return jsonify({
            'error': 'Error deleting file',
            'message': str(e)
        }), 500

@app.route('/files', methods=['GET'])
def list_files():
    """List all uploaded files"""
    db, fs, files_collection = get_db()
    
    if not db:
        return jsonify({'error': 'Database not connected'}), 500
    
    try:
        files = []
        for doc in files_collection.find().sort('uploaded_at', -1).limit(100):
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
    
    except Exception as e:
        return jsonify({
            'error': 'Error listing files',
            'message': str(e)
        }), 500

@app.route('/info/<code>', methods=['GET'])
def file_info(code):
    """Get file information by code"""
    db, fs, files_collection = get_db()
    
    if not db:
        return jsonify({'error': 'Database not connected'}), 500
    
    try:
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
    
    except Exception as e:
        return jsonify({
            'error': 'Error getting file info',
            'message': str(e)
        }), 500
