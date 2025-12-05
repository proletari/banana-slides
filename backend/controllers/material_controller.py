"""
Material Controller - handles standalone material image generation
"""
from flask import Blueprint, request, current_app
from models import db, Project, Material
from utils import success_response, error_response, not_found, bad_request
from services import AIService, FileService
from pathlib import Path
from werkzeug.utils import secure_filename
import tempfile
import shutil
from PIL import Image


material_bp = Blueprint('materials', __name__, url_prefix='/api/projects')
material_global_bp = Blueprint('materials_global', __name__, url_prefix='/api/materials')


@material_bp.route('/<project_id>/materials/generate', methods=['POST'])
def generate_material_image(project_id):
    """
    POST /api/projects/{project_id}/materials/generate - Generate a standalone material image

    支持 multipart/form-data：
    - prompt: 文生图提示词（将被直接传给模型，不做任何修饰）
    - ref_image: 主参考图（可选）
    - extra_images: 额外参考图（可多文件，可选）
    """
    try:
        project = Project.query.get(project_id)
        if not project:
            return not_found('Project')

        # 解析请求数据（优先支持 multipart，用于文件上传）
        if request.is_json:
            data = request.get_json() or {}
            prompt = data.get('prompt', '').strip()
            ref_file = None
            extra_files = []
        else:
            data = request.form.to_dict()
            prompt = (data.get('prompt') or '').strip()
            ref_file = request.files.get('ref_image')
            # 支持多张额外参考图
            extra_files = request.files.getlist('extra_images') or []

        if not prompt:
            return bad_request("prompt is required")

        # 初始化服务
        ai_service = AIService(
            current_app.config['GOOGLE_API_KEY'],
            current_app.config['GOOGLE_API_BASE']
        )
        file_service = FileService(current_app.config['UPLOAD_FOLDER'])

        temp_dir = Path(tempfile.mkdtemp(dir=current_app.config['UPLOAD_FOLDER']))

        try:
            ref_path = None
            # 如果提供了主参考图，则保存到临时目录
            if ref_file and ref_file.filename:
                ref_filename = secure_filename(ref_file.filename or 'ref.png')
                ref_path = temp_dir / ref_filename
                ref_file.save(str(ref_path))

            # 保存额外参考图到临时目录
            additional_ref_images = []
            for extra in extra_files:
                if not extra or not extra.filename:
                    continue
                extra_filename = secure_filename(extra.filename)
                extra_path = temp_dir / extra_filename
                extra.save(str(extra_path))
                additional_ref_images.append(str(extra_path))

            # 使用用户原始 prompt 直接调用文生图模型（主参考图可选）
            image = ai_service.generate_image(
                prompt=prompt,
                ref_image_path=str(ref_path) if ref_path else None,
                aspect_ratio=current_app.config['DEFAULT_ASPECT_RATIO'],
                resolution=current_app.config['DEFAULT_RESOLUTION'],
                additional_ref_images=additional_ref_images or None,
            )

            if not image:
                return error_response('AI_SERVICE_ERROR', 'Failed to generate image', 503)

            # 保存生成的素材图片
            relative_path = file_service.save_material_image(image, project_id)
            # relative_path 形如 "<project_id>/materials/xxx.png"
            relative = Path(relative_path)
            # materials 目录下的文件名
            filename = relative.name

            # 构造前端可访问的 URL
            image_url = file_service.get_file_url(project_id, 'materials', filename)

            # 保存素材信息到数据库
            material = Material(
                project_id=project_id,
                filename=filename,
                relative_path=relative_path,
                url=image_url
            )
            db.session.add(material)
            
            # 不改变项目结构，仅更新时间以便前端刷新
            project.updated_at = project.updated_at  # 不强制变更，仅保持兼容
            db.session.commit()

            return success_response({
                "image_url": image_url,
                "relative_path": relative_path,
                "material_id": material.id,
            })
        finally:
            # 清理临时目录
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        db.session.rollback()
        return error_response('AI_SERVICE_ERROR', str(e), 503)


@material_bp.route('/<project_id>/materials', methods=['GET'])
def list_materials(project_id):
    """
    GET /api/projects/{project_id}/materials - List materials
    
    Query params:
        - project_id: Optional filter by project_id (can be 'all' to get all materials, 'none' to get materials without project)
    
    Returns:
        List of material images with filename, url, and metadata
    """
    try:
        # 支持查询参数来筛选项目
        filter_project_id = request.args.get('project_id', project_id)
        
        # 从数据库查询素材
        query = Material.query
        
        if filter_project_id == 'all':
            # 获取所有素材（包括有 project_id 和没有 project_id 的）
            pass  # 不添加任何过滤条件
        elif filter_project_id == 'none':
            # 只获取没有关联项目的素材
            query = query.filter(Material.project_id.is_(None))
        else:
            # 验证项目是否存在
            project = Project.query.get(filter_project_id)
            if not project:
                return not_found('Project')
            # 获取指定项目的素材
            query = query.filter(Material.project_id == filter_project_id)
        
        materials = query.order_by(Material.created_at.desc()).all()
        
        # 转换为字典格式
        materials_list = [material.to_dict() for material in materials]
        
        return success_response({
            "materials": materials_list,
            "count": len(materials_list)
        })
    
    except Exception as e:
        return error_response('SERVER_ERROR', str(e), 500)


@material_bp.route('/<project_id>/materials/upload', methods=['POST'])
def upload_material(project_id):
    """
    POST /api/projects/{project_id}/materials/upload - Upload a material image
    
    支持 multipart/form-data：
    - file: 图片文件（必需）
    - project_id: 可选的查询参数，如果不提供则使用路径中的 project_id，如果为 'none' 则不关联项目
    
    Returns:
        Material info with filename, url, and metadata
    """
    try:
        # 支持通过查询参数指定 project_id，如果为 'none' 则不关联项目
        target_project_id = request.args.get('project_id', project_id)
        if target_project_id == 'none':
            target_project_id = None
        elif target_project_id and target_project_id != 'all':
            # 验证项目是否存在
            project = Project.query.get(target_project_id)
            if not project:
                return not_found('Project')
        
        # 获取上传的文件
        if 'file' not in request.files:
            return bad_request("file is required")
        
        file = request.files['file']
        if not file or not file.filename:
            return bad_request("file is required")
        
        # 验证文件类型
        allowed_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'}
        filename = secure_filename(file.filename)
        file_ext = Path(filename).suffix.lower()
        if file_ext not in allowed_extensions:
            return bad_request(f"Unsupported file type. Allowed: {', '.join(allowed_extensions)}")
        
        file_service = FileService(current_app.config['UPLOAD_FOLDER'])
        
        # 如果有关联项目，保存到项目目录；否则保存到通用素材目录
        if target_project_id:
            materials_dir = file_service._get_materials_dir(target_project_id)
        else:
            # 保存到通用素材目录
            materials_dir = file_service.upload_folder / "materials"
            materials_dir.mkdir(exist_ok=True, parents=True)
        
        # 生成唯一文件名
        import time
        timestamp = int(time.time() * 1000)
        base_name = Path(filename).stem
        unique_filename = f"{base_name}_{timestamp}{file_ext}"
        
        filepath = materials_dir / unique_filename
        file.save(str(filepath))
        
        # 计算相对路径
        relative_path = str(filepath.relative_to(file_service.upload_folder))
        
        # 构造前端可访问的 URL
        if target_project_id:
            image_url = file_service.get_file_url(target_project_id, 'materials', unique_filename)
        else:
            # 通用素材的 URL
            image_url = f"/files/materials/{unique_filename}"
        
        # 保存素材信息到数据库
        material = Material(
            project_id=target_project_id,
            filename=unique_filename,
            relative_path=relative_path,
            url=image_url
        )
        db.session.add(material)
        db.session.commit()
        
        return success_response(material.to_dict(), status_code=201)
    
    except Exception as e:
        db.session.rollback()
        return error_response('SERVER_ERROR', str(e), 500)


@material_global_bp.route('', methods=['GET'])
def list_all_materials():
    """
    GET /api/materials - List all materials (global, not bound to a project)
    
    Query params:
        - project_id: Optional filter by project_id (can be 'all' to get all materials, 'none' to get materials without project)
    
    Returns:
        List of material images with filename, url, and metadata
    """
    try:
        # 支持查询参数来筛选项目
        filter_project_id = request.args.get('project_id', 'all')
        
        # 从数据库查询素材
        query = Material.query
        
        if filter_project_id == 'all':
            # 获取所有素材（包括有 project_id 和没有 project_id 的）
            pass  # 不添加任何过滤条件
        elif filter_project_id == 'none':
            # 只获取没有关联项目的素材
            query = query.filter(Material.project_id.is_(None))
        else:
            # 获取指定项目的素材
            query = query.filter(Material.project_id == filter_project_id)
        
        materials = query.order_by(Material.created_at.desc()).all()
        
        # 转换为字典格式
        materials_list = [material.to_dict() for material in materials]
        
        return success_response({
            "materials": materials_list,
            "count": len(materials_list)
        })
    
    except Exception as e:
        return error_response('SERVER_ERROR', str(e), 500)


@material_global_bp.route('/upload', methods=['POST'])
def upload_material_global():
    """
    POST /api/materials/upload - Upload a material image (global, not bound to a project)
    
    支持 multipart/form-data：
    - file: 图片文件（必需）
    - project_id: 可选的查询参数，如果提供则关联到项目，如果不提供或为 'none' 则不关联项目
    
    Returns:
        Material info with filename, url, and metadata
    """
    try:
        # 支持通过查询参数指定 project_id，如果为 'none' 或不提供则不关联项目
        target_project_id = request.args.get('project_id')
        if target_project_id == 'none' or not target_project_id:
            target_project_id = None
        elif target_project_id:
            # 验证项目是否存在
            project = Project.query.get(target_project_id)
            if not project:
                return not_found('Project')
        
        # 获取上传的文件
        if 'file' not in request.files:
            return bad_request("file is required")
        
        file = request.files['file']
        if not file or not file.filename:
            return bad_request("file is required")
        
        # 验证文件类型
        allowed_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'}
        filename = secure_filename(file.filename)
        file_ext = Path(filename).suffix.lower()
        if file_ext not in allowed_extensions:
            return bad_request(f"Unsupported file type. Allowed: {', '.join(allowed_extensions)}")
        
        file_service = FileService(current_app.config['UPLOAD_FOLDER'])
        
        # 如果有关联项目，保存到项目目录；否则保存到通用素材目录
        if target_project_id:
            materials_dir = file_service._get_materials_dir(target_project_id)
        else:
            # 保存到通用素材目录
            materials_dir = file_service.upload_folder / "materials"
            materials_dir.mkdir(exist_ok=True, parents=True)
        
        # 生成唯一文件名
        import time
        timestamp = int(time.time() * 1000)
        base_name = Path(filename).stem
        unique_filename = f"{base_name}_{timestamp}{file_ext}"
        
        filepath = materials_dir / unique_filename
        file.save(str(filepath))
        
        # 计算相对路径
        relative_path = str(filepath.relative_to(file_service.upload_folder))
        
        # 构造前端可访问的 URL
        if target_project_id:
            image_url = file_service.get_file_url(target_project_id, 'materials', unique_filename)
        else:
            # 通用素材的 URL
            image_url = f"/files/materials/{unique_filename}"
        
        # 保存素材信息到数据库
        material = Material(
            project_id=target_project_id,
            filename=unique_filename,
            relative_path=relative_path,
            url=image_url
        )
        db.session.add(material)
        db.session.commit()
        
        return success_response(material.to_dict(), status_code=201)
    
    except Exception as e:
        db.session.rollback()
        return error_response('SERVER_ERROR', str(e), 500)


