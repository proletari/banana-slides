"""Database models package"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

from .project import Project
from .page import Page
from .task import Task
from .user_template import UserTemplate
from .page_image_version import PageImageVersion

__all__ = ['db', 'Project', 'Page', 'Task', 'UserTemplate', 'PageImageVersion']

