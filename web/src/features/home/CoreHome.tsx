import { useState } from "react";
import {coreProduct} from '@platform/embedded';
import { HomeInfrastructure } from "./HomeInfrastructure";
import { HomeApplicationFolders } from "./HomeApplicationFolders";
import { useApplications } from "@features/applications/catalog";
import { HomeHeroPet } from "./HomeHeroPet";
import { CompactPanelTabs } from "@shared/ui/CompactPanelTabs";
import { FlaskConical, History, Folder, Network } from "lucide-react";
import "./home.css";
export function Home({ navigate }: { navigate: (p: string) => void }) {
  const applications = useApplications();
  const [mode, setMode] = useState("chat");
  const product = coreProduct();
  return (
    <section className="page home-page">
      <header className="home-header">
        <div className="home-header-copy">
          <span className="home-eyebrow">你的灵感，随时开场</span>
          <h1>{product?.name || '今天想从哪里开始？'}</h1>
          <p>从一个好想法，到下一件作品。</p>
        </div>
        <HomeHeroPet />
      </header>
      <div className="home-primary core-home-with-apps">
        <section
          className="home-applications home-panel"
          aria-labelledby="home-app-title"
        >
          <header className="home-section-heading">
            <h2 id="home-app-title">{product ? '项目' : 'AI 应用'}</h2>
            <span className="home-count">
              {product ? product.name : `${applications.data?.length || 0} 个应用`}
            </span>
            <button
              className="home-text-link"
              onClick={() => navigate("/apps")}
            >
              {product ? '查看项目' : '全部应用'}
            </button>
          </header>
          {product ? <a className="home-resource-link" href={product.projectsUrl}><Folder /><span><strong>{product.name}</strong><small>打开项目列表，继续创作或新建项目</small></span></a> : <HomeApplicationFolders
            applications={applications.data || []}
            onNavigate={navigate}
          />}
          {!product && !applications.data?.length && (
            <p className="core-apps-hint">
              在 DSH 中安装应用插件，连接后会显示在这里。
              <button
                className="home-text-link"
                onClick={() => navigate("/apps")}
              >
                安装应用
              </button>
            </p>
          )}
        </section>
        <section className="home-ai">
          <header className="home-section-heading">
            <h2>AI 能力</h2>
            <p>从模型接入，到效果验证。</p>
          </header>
          <HomeInfrastructure onNavigate={navigate} />
          <div className="home-ai-feature home-test-feature home-panel">
            <div className="home-feature-copy">
              <span className="home-copper-icon">
                <FlaskConical />
              </span>
              <h3>模型测试</h3>
              <p>
                对话、图像、视频
                <br />
                与语音测试
              </p>
            </div>
            <div className="home-test-preview">
              <CompactPanelTabs
                label="首页测试模式"
                value={mode}
                onChange={setMode}
                options={[
                  { value: "chat", label: "对话" },
                  { value: "image", label: "图像" },
                  { value: "video", label: "视频" },
                  { value: "synthesis", label: "语音" },
                ]}
              />
              <div className="home-test-draft">
                <span>选择模型，开始一次测试</span>
                <div>
                  <span className="home-test-hint">参数在测试台配置</span>
                  <button onClick={() => navigate("/model-test?mode=" + mode)}>
                    开始测试
                  </button>
                </div>
              </div>
            </div>
          </div>
          <button
            className="home-panel home-mini-link"
            onClick={() => navigate("/model-call-logs")}
          >
            <span className="home-copper-icon">
              <History />
            </span>
            <span>
              <strong>调用日志</strong>
              <small>用量、耗时与错误记录</small>
            </span>
          </button>
        </section>
        <section className="home-panel core-home-resources">
          <div className="core-home-links">
            {!coreProduct() && <>
            <button
              className="home-resource-link"
              onClick={() => navigate("/files")}
            >
              <Folder />
              <span>
                <strong>文件管理</strong>
                <small>上传参考素材、查看生成结果</small>
              </span>
            </button>
            </>}
            <button
              className="home-resource-link"
              onClick={() =>
                navigate("/integrations?kind=external-connections")
              }
            >
              <Network />
              <span>
                <strong>外部连接</strong>
                <small>连接 DeepSeek Harness</small>
              </span>
            </button>
          </div>
        </section>
      </div>
    </section>
  );
}
