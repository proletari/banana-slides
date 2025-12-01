import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Home, Clock, FileText, ChevronRight, Trash2 } from 'lucide-react';
import { Button, Loading, Card, useToast, useConfirm } from '@/components/shared';
import { useProjectStore } from '@/store/useProjectStore';
import * as api from '@/api/endpoints';
import { getImageUrl } from '@/api/client';
import { normalizeProject } from '@/utils';
import type { Project } from '@/types';

export const History: React.FC = () => {
  const navigate = useNavigate();
  const { syncProject, setCurrentProject } = useProjectStore();
  
  const [projects, setProjects] = useState<Project[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedProjects, setSelectedProjects] = useState<Set<string>>(new Set());
  const [isDeleting, setIsDeleting] = useState(false);
  const { show, ToastContainer } = useToast();
  const { confirm, ConfirmDialog } = useConfirm();

  useEffect(() => {
    loadProjects();
  }, []);

  const loadProjects = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.listProjects(50, 0);
      if (response.data?.projects) {
        const normalizedProjects = response.data.projects.map(normalizeProject);
        setProjects(normalizedProjects);
      }
    } catch (err: any) {
      console.error('加载历史项目失败:', err);
      setError(err.message || '加载历史项目失败');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSelectProject = async (project: Project) => {
    const projectId = project.id || project.project_id;
    if (!projectId) return;

    // 如果正在批量选择模式，不跳转
    if (selectedProjects.size > 0) {
      return;
    }

    try {
      // 设置当前项目
      setCurrentProject(project);
      localStorage.setItem('currentProjectId', projectId);
      
      // 同步项目数据
      await syncProject(projectId);
      
      // 根据项目状态跳转到不同页面，并传递来源信息
      const navigateOptions = { state: { from: 'history' } };
      if (project.pages && project.pages.length > 0) {
        // 检查是否有生成的图片
        const hasImages = project.pages.some(p => p.generated_image_path);
        if (hasImages) {
          navigate(`/project/${projectId}/preview`, navigateOptions);
        } else {
          // 检查是否有描述
          const hasDescriptions = project.pages.some(p => p.description_content);
          if (hasDescriptions) {
            navigate(`/project/${projectId}/detail`, navigateOptions);
          } else {
            navigate(`/project/${projectId}/outline`, navigateOptions);
          }
        }
      } else {
        // 没有页面，跳转到大纲编辑
        navigate(`/project/${projectId}/outline`, navigateOptions);
      }
    } catch (err: any) {
      console.error('打开项目失败:', err);
      show({ 
        message: '打开项目失败: ' + (err.message || '未知错误'), 
        type: 'error' 
      });
    }
  };

  const handleDeleteProject = async (e: React.MouseEvent, project: Project) => {
    e.stopPropagation(); // 阻止事件冒泡，避免触发项目选择
    
    const projectId = project.id || project.project_id;
    if (!projectId) return;

    const projectTitle = project.idea_prompt || '未命名项目';
    confirm(
      `确定要删除项目"${projectTitle}"吗？此操作不可恢复。`,
      async () => {
        await deleteProjects([projectId]);
      },
      { title: '确认删除', variant: 'danger' }
    );
  };

  const handleToggleSelect = (projectId: string) => {
    const newSelected = new Set(selectedProjects);
    if (newSelected.has(projectId)) {
      newSelected.delete(projectId);
    } else {
      newSelected.add(projectId);
    }
    setSelectedProjects(newSelected);
  };

  const handleSelectAll = () => {
    if (selectedProjects.size === projects.length) {
      // 全部选中，则取消全选
      setSelectedProjects(new Set());
    } else {
      // 全选
      const allIds = projects.map(p => p.id || p.project_id).filter(Boolean) as string[];
      setSelectedProjects(new Set(allIds));
    }
  };

  const handleBatchDelete = async () => {
    if (selectedProjects.size === 0) return;

    const count = selectedProjects.size;
    confirm(
      `确定要删除选中的 ${count} 个项目吗？此操作不可恢复。`,
      async () => {
        const projectIds = Array.from(selectedProjects);
        await deleteProjects(projectIds);
      },
      { title: '确认批量删除', variant: 'danger' }
    );
  };

  const deleteProjects = async (projectIds: string[]) => {
    setIsDeleting(true);
    const currentProjectId = localStorage.getItem('currentProjectId');
    let deletedCurrentProject = false;

    try {
      // 批量删除
      const deletePromises = projectIds.map(projectId => api.deleteProject(projectId));
      await Promise.all(deletePromises);

      // 检查是否删除了当前项目
      if (currentProjectId && projectIds.includes(currentProjectId)) {
        localStorage.removeItem('currentProjectId');
        setCurrentProject(null);
        deletedCurrentProject = true;
      }

      // 从列表中移除已删除的项目
      setProjects(projects.filter(p => {
        const id = p.id || p.project_id;
        return id && !projectIds.includes(id);
      }));

      // 清空选择
      setSelectedProjects(new Set());

      if (deletedCurrentProject) {
        show({ 
          message: '已删除项目，包括当前打开的项目', 
          type: 'info' 
        });
      } else {
        show({ 
          message: `成功删除 ${projectIds.length} 个项目`, 
          type: 'success' 
        });
      }
    } catch (err: any) {
      console.error('删除项目失败:', err);
      show({ 
        message: '删除项目失败: ' + (err.message || '未知错误'), 
        type: 'error' 
      });
    } finally {
      setIsDeleting(false);
    }
  };

  const getFirstPageImage = (project: Project): string | null => {
    if (!project.pages || project.pages.length === 0) {
      return null;
    }
    
    // 找到第一页有图片的页面
    const firstPageWithImage = project.pages.find(p => p.generated_image_path);
    if (firstPageWithImage?.generated_image_path) {
      return getImageUrl(firstPageWithImage.generated_image_path, firstPageWithImage.updated_at);
    }
    
    return null;
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const getStatusText = (project: Project) => {
    if (!project.pages || project.pages.length === 0) {
      return '未开始';
    }
    const hasImages = project.pages.some(p => p.generated_image_path);
    if (hasImages) {
      return '已完成';
    }
    const hasDescriptions = project.pages.some(p => p.description_content);
    if (hasDescriptions) {
      return '待生成图片';
    }
    return '待生成描述';
  };

  const getStatusColor = (project: Project) => {
    const status = getStatusText(project);
    if (status === '已完成') return 'text-green-600 bg-green-50';
    if (status === '待生成图片') return 'text-yellow-600 bg-yellow-50';
    if (status === '待生成描述') return 'text-blue-600 bg-blue-50';
    return 'text-gray-600 bg-gray-50';
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-banana-50 via-white to-gray-50">
      {/* 导航栏 */}
      <nav className="h-16 bg-white shadow-sm border-b border-gray-100">
        <div className="max-w-7xl mx-auto px-4 h-full flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-10 h-10 bg-gradient-to-br from-banana-500 to-banana-600 rounded-lg flex items-center justify-center text-2xl">
              🍌
            </div>
            <span className="text-xl font-bold text-gray-900">蕉幻</span>
          </div>
          <div className="flex items-center gap-4">
            <Button
              variant="ghost"
              size="sm"
              icon={<Home size={18} />}
              onClick={() => navigate('/')}
            >
              主页
            </Button>
          </div>
        </div>
      </nav>

      {/* 主内容 */}
      <main className="max-w-6xl mx-auto px-4 py-8">
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 mb-2">历史项目</h1>
            <p className="text-gray-600">查看和管理你的所有项目</p>
          </div>
          {projects.length > 0 && selectedProjects.size > 0 && (
            <div className="flex items-center gap-3">
              <span className="text-sm text-gray-600">
                已选择 {selectedProjects.size} 项
              </span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setSelectedProjects(new Set())}
                disabled={isDeleting}
              >
                取消选择
              </Button>
              <Button
                variant="secondary"
                size="sm"
                icon={<Trash2 size={16} />}
                onClick={handleBatchDelete}
                disabled={isDeleting}
                loading={isDeleting}
              >
                批量删除
              </Button>
            </div>
          )}
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loading message="加载中..." />
          </div>
        ) : error ? (
          <Card className="p-8 text-center">
            <div className="text-6xl mb-4">⚠️</div>
            <p className="text-gray-600 mb-4">{error}</p>
            <Button variant="primary" onClick={loadProjects}>
              重试
            </Button>
          </Card>
        ) : projects.length === 0 ? (
          <Card className="p-12 text-center">
            <div className="text-6xl mb-4">📭</div>
            <h3 className="text-xl font-semibold text-gray-700 mb-2">
              暂无历史项目
            </h3>
            <p className="text-gray-500 mb-6">
              创建你的第一个项目开始使用吧
            </p>
            <Button variant="primary" onClick={() => navigate('/')}>
              创建新项目
            </Button>
          </Card>
        ) : (
          <div className="space-y-4">
            {/* 全选工具栏 */}
            {projects.length > 0 && (
              <div className="flex items-center gap-3 pb-2 border-b border-gray-200">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedProjects.size === projects.length && projects.length > 0}
                    onChange={handleSelectAll}
                    className="w-4 h-4 text-banana-600 border-gray-300 rounded focus:ring-banana-500"
                  />
                  <span className="text-sm text-gray-700">
                    {selectedProjects.size === projects.length ? '取消全选' : '全选'}
                  </span>
                </label>
              </div>
            )}
            
            {projects.map((project) => {
              const projectId = project.id || project.project_id;
              if (!projectId) return null;
              
              const title = project.idea_prompt || '未命名项目';
              const pageCount = project.pages?.length || 0;
              const statusText = getStatusText(project);
              const statusColor = getStatusColor(project);
              const firstPageImage = getFirstPageImage(project);
              const isSelected = selectedProjects.has(projectId);
              
              return (
                <Card
                  key={projectId}
                  className={`p-6 transition-all ${
                    isSelected 
                      ? 'border-2 border-banana-500 bg-banana-50' 
                      : 'hover:shadow-lg border border-gray-200'
                  } ${selectedProjects.size > 0 ? 'cursor-default' : 'cursor-pointer'}`}
                  onClick={() => handleSelectProject(project)}
                >
                  <div className="flex items-start gap-4">
                    {/* 复选框 */}
                    <div className="pt-1" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => handleToggleSelect(projectId)}
                        className="w-4 h-4 text-banana-600 border-gray-300 rounded focus:ring-banana-500 cursor-pointer"
                      />
                    </div>
                    
                    {/* 左侧：项目信息 */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-3 mb-2">
                        <h3 className="text-lg font-semibold text-gray-900 truncate">
                          {title}
                        </h3>
                        <span className={`px-2 py-1 rounded text-xs font-medium ${statusColor}`}>
                          {statusText}
                        </span>
                      </div>
                      <div className="flex items-center gap-4 text-sm text-gray-500">
                        <span className="flex items-center gap-1">
                          <FileText size={14} />
                          {pageCount} 页
                        </span>
                        <span className="flex items-center gap-1">
                          <Clock size={14} />
                          {formatDate(project.updated_at || project.created_at)}
                        </span>
                      </div>
                    </div>
                    
                    {/* 右侧：图片预览和操作 */}
                    <div className="flex items-center gap-3">
                      {/* 图片预览 */}
                      <div className="w-64 h-36 rounded-lg overflow-hidden bg-gray-100 border border-gray-200 flex-shrink-0">
                        {firstPageImage ? (
                          <img
                            src={firstPageImage}
                            alt="第一页预览"
                            className="w-full h-full object-cover"
                          />
                        ) : (
                          <div className="w-full h-full flex items-center justify-center text-gray-400">
                            <FileText size={24} />
                          </div>
                        )}
                      </div>
                      
                      {/* 删除按钮 */}
                      <button
                        onClick={(e) => handleDeleteProject(e, project)}
                        className="p-2 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors flex-shrink-0"
                        title="删除项目"
                      >
                        <Trash2 size={18} />
                      </button>
                      
                      {/* 右箭头 */}
                      <ChevronRight size={20} className="text-gray-400 flex-shrink-0" />
                    </div>
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </main>
      <ToastContainer />
      {ConfirmDialog}
    </div>
  );
};

