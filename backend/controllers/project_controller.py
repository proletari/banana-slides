"""
Project Controller - handles project-related endpoints
"""
from flask import Blueprint, request, jsonify
from models import db, Project, Page, Task
from utils import success_response, error_response, not_found, bad_request
from services import AIService
from services.task_manager import task_manager, generate_descriptions_task, generate_images_task
import json
from datetime import datetime

project_bp = Blueprint('projects', __name__, url_prefix='/api/projects')


@project_bp.route('', methods=['GET'])
def list_projects():
    """
    GET /api/projects - Get all projects (for history)
    
    Query params:
    - limit: number of projects to return (default: 50)
    - offset: offset for pagination (default: 0)
    """
    try:
        from sqlalchemy import desc
        
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        # Get projects ordered by updated_at descending
        projects = Project.query.order_by(desc(Project.updated_at)).limit(limit).offset(offset).all()
        
        return success_response({
            'projects': [project.to_dict(include_pages=True) for project in projects],
            'total': Project.query.count()
        })
    
    except Exception as e:
        return error_response('SERVER_ERROR', str(e), 500)


@project_bp.route('', methods=['POST'])
def create_project():
    """
    POST /api/projects - Create a new project
    
    Request body:
    {
        "creation_type": "idea|outline|descriptions",
        "idea_prompt": "...",  # required for idea type
        "template_id": "optional"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return bad_request("Request body is required")
        
        creation_type = data.get('creation_type', 'idea')
        
        if creation_type not in ['idea', 'outline', 'descriptions']:
            return bad_request("Invalid creation_type")
        
        # Create project
        project = Project(
            creation_type=creation_type,
            idea_prompt=data.get('idea_prompt'),
            status='DRAFT'
        )
        
        db.session.add(project)
        db.session.commit()
        
        return success_response({
            'project_id': project.id,
            'status': project.status,
            'pages': []
        }, status_code=201)
    
    except Exception as e:
        db.session.rollback()
        return error_response('SERVER_ERROR', str(e), 500)


@project_bp.route('/<project_id>', methods=['GET'])
def get_project(project_id):
    """
    GET /api/projects/{project_id} - Get project details
    """
    try:
        project = Project.query.get(project_id)
        
        if not project:
            return not_found('Project')
        
        return success_response(project.to_dict(include_pages=True))
    
    except Exception as e:
        return error_response('SERVER_ERROR', str(e), 500)


@project_bp.route('/<project_id>', methods=['PUT'])
def update_project(project_id):
    """
    PUT /api/projects/{project_id} - Update project
    
    Request body:
    {
        "idea_prompt": "...",
        "pages_order": ["page-uuid-1", "page-uuid-2", ...]
    }
    """
    try:
        project = Project.query.get(project_id)
        
        if not project:
            return not_found('Project')
        
        data = request.get_json()
        
        # Update idea_prompt if provided
        if 'idea_prompt' in data:
            project.idea_prompt = data['idea_prompt']
        
        # Update page order if provided
        if 'pages_order' in data:
            pages_order = data['pages_order']
            for index, page_id in enumerate(pages_order):
                page = Page.query.get(page_id)
                if page and page.project_id == project_id:
                    page.order_index = index
        
        project.updated_at = datetime.utcnow()
        db.session.commit()
        
        return success_response(project.to_dict(include_pages=True))
    
    except Exception as e:
        db.session.rollback()
        return error_response('SERVER_ERROR', str(e), 500)


@project_bp.route('/<project_id>', methods=['DELETE'])
def delete_project(project_id):
    """
    DELETE /api/projects/{project_id} - Delete project
    """
    try:
        project = Project.query.get(project_id)
        
        if not project:
            return not_found('Project')
        
        # Delete project files
        from services import FileService
        from flask import current_app
        file_service = FileService(current_app.config['UPLOAD_FOLDER'])
        file_service.delete_project_files(project_id)
        
        # Delete project from database (cascade will delete pages and tasks)
        db.session.delete(project)
        db.session.commit()
        
        return success_response(message="Project deleted successfully")
    
    except Exception as e:
        db.session.rollback()
        return error_response('SERVER_ERROR', str(e), 500)


@project_bp.route('/<project_id>/generate/outline', methods=['POST'])
def generate_outline(project_id):
    """
    POST /api/projects/{project_id}/generate/outline - Generate outline
    
    Request body:
    {
        "idea_prompt": "...",
        "outline_format": "simple|part_based|auto"
    }
    """
    try:
        project = Project.query.get(project_id)
        
        if not project:
            return not_found('Project')
        
        data = request.get_json()
        idea_prompt = data.get('idea_prompt') or project.idea_prompt
        
        if not idea_prompt:
            return bad_request("idea_prompt is required")
        
        # Initialize AI service
        from flask import current_app
        ai_service = AIService(
            current_app.config['GOOGLE_API_KEY'],
            current_app.config['GOOGLE_API_BASE']
        )
        
        # Generate outline
        outline = ai_service.generate_outline(idea_prompt)
        
        # Flatten outline to pages
        pages_data = ai_service.flatten_outline(outline)
        
        # Delete existing pages
        Page.query.filter_by(project_id=project_id).delete()
        
        # Create pages from outline
        pages_list = []
        for i, page_data in enumerate(pages_data):
            page = Page(
                project_id=project_id,
                order_index=i,
                part=page_data.get('part'),
                status='DRAFT'
            )
            page.set_outline_content({
                'title': page_data.get('title'),
                'points': page_data.get('points', [])
            })
            
            db.session.add(page)
            pages_list.append(page)
        
        # Update project status
        project.status = 'OUTLINE_GENERATED'
        project.idea_prompt = idea_prompt
        project.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        # Return pages
        return success_response({
            'pages': [page.to_dict() for page in pages_list]
        })
    
    except Exception as e:
        db.session.rollback()
        return error_response('AI_SERVICE_ERROR', str(e), 503)


@project_bp.route('/<project_id>/generate/descriptions', methods=['POST'])
def generate_descriptions(project_id):
    """
    POST /api/projects/{project_id}/generate/descriptions - Generate descriptions
    
    Request body:
    {
        "max_workers": 5
    }
    """
    try:
        project = Project.query.get(project_id)
        
        if not project:
            return not_found('Project')
        
        if project.status not in ['OUTLINE_GENERATED', 'DRAFT', 'DESCRIPTIONS_GENERATED']:
            return bad_request("Project must have outline generated first")
        
        # Get pages
        pages = Page.query.filter_by(project_id=project_id).order_by(Page.order_index).all()
        
        if not pages:
            return bad_request("No pages found for project")
        
        # Reconstruct outline from pages
        outline = []
        for page in pages:
            outline_content = page.get_outline_content()
            if outline_content:
                page_data = outline_content.copy()
                if page.part:
                    page_data['part'] = page.part
                outline.append(page_data)
        
        data = request.get_json() or {}
        from flask import current_app
        # 从配置中读取默认并发数，如果请求中提供了则使用请求的值
        max_workers = data.get('max_workers', current_app.config.get('MAX_DESCRIPTION_WORKERS', 5))
        
        # Create task
        task = Task(
            project_id=project_id,
            task_type='GENERATE_DESCRIPTIONS',
            status='PENDING'
        )
        task.set_progress({
            'total': len(pages),
            'completed': 0,
            'failed': 0
        })
        
        db.session.add(task)
        db.session.commit()
        
        # Initialize AI service
        ai_service = AIService(
            current_app.config['GOOGLE_API_KEY'],
            current_app.config['GOOGLE_API_BASE']
        )
        
        # Get app instance for background task
        app = current_app._get_current_object()
        
        # Submit background task
        task_manager.submit_task(
            task.id,
            generate_descriptions_task,
            project_id,
            ai_service,
            project.idea_prompt,
            outline,
            max_workers,
            app
        )
        
        # Update project status
        project.status = 'GENERATING_DESCRIPTIONS'
        db.session.commit()
        
        return success_response({
            'task_id': task.id,
            'status': 'GENERATING_DESCRIPTIONS',
            'total_pages': len(pages)
        }, status_code=202)
    
    except Exception as e:
        db.session.rollback()
        return error_response('SERVER_ERROR', str(e), 500)


@project_bp.route('/<project_id>/generate/images', methods=['POST'])
def generate_images(project_id):
    """
    POST /api/projects/{project_id}/generate/images - Generate images
    
    Request body:
    {
        "max_workers": 8,
        "use_template": true
    }
    """
    try:
        project = Project.query.get(project_id)
        
        if not project:
            return not_found('Project')
        
        if project.status not in ['DESCRIPTIONS_GENERATED', 'OUTLINE_GENERATED']:
            return bad_request("Project must have descriptions generated first")
        
        # Get pages
        pages = Page.query.filter_by(project_id=project_id).order_by(Page.order_index).all()
        
        if not pages:
            return bad_request("No pages found for project")
        
        # Reconstruct outline from pages
        outline = []
        for page in pages:
            outline_content = page.get_outline_content()
            if outline_content:
                page_data = outline_content.copy()
                if page.part:
                    page_data['part'] = page.part
                outline.append(page_data)
        
        data = request.get_json() or {}
        from flask import current_app
        # 从配置中读取默认并发数，如果请求中提供了则使用请求的值
        max_workers = data.get('max_workers', current_app.config.get('MAX_IMAGE_WORKERS', 8))
        use_template = data.get('use_template', True)
        
        # Create task
        task = Task(
            project_id=project_id,
            task_type='GENERATE_IMAGES',
            status='PENDING'
        )
        task.set_progress({
            'total': len(pages),
            'completed': 0,
            'failed': 0
        })
        
        db.session.add(task)
        db.session.commit()
        
        # Initialize services
        ai_service = AIService(
            current_app.config['GOOGLE_API_KEY'],
            current_app.config['GOOGLE_API_BASE']
        )
        
        from services import FileService
        file_service = FileService(current_app.config['UPLOAD_FOLDER'])
        
        # Get app instance for background task
        app = current_app._get_current_object()
        
        # Submit background task
        task_manager.submit_task(
            task.id,
            generate_images_task,
            project_id,
            ai_service,
            file_service,
            outline,
            use_template,
            max_workers,
            current_app.config['DEFAULT_ASPECT_RATIO'],
            current_app.config['DEFAULT_RESOLUTION'],
            app
        )
        
        # Update project status
        project.status = 'GENERATING_IMAGES'
        db.session.commit()
        
        return success_response({
            'task_id': task.id,
            'status': 'GENERATING_IMAGES',
            'total_pages': len(pages)
        }, status_code=202)
    
    except Exception as e:
        db.session.rollback()
        return error_response('SERVER_ERROR', str(e), 500)


@project_bp.route('/<project_id>/tasks/<task_id>', methods=['GET'])
def get_task_status(project_id, task_id):
    """
    GET /api/projects/{project_id}/tasks/{task_id} - Get task status
    """
    try:
        task = Task.query.get(task_id)
        
        if not task or task.project_id != project_id:
            return not_found('Task')
        
        return success_response(task.to_dict())
    
    except Exception as e:
        return error_response('SERVER_ERROR', str(e), 500)

