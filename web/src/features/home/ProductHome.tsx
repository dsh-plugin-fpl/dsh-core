import { Sparkles, Link2, Box, ArrowUpRight } from "lucide-react";
import { coreProduct } from "@platform/embedded";
import { HomeHeroPet } from "./HomeHeroPet";
import "./product-home.css";
const areas = [
  {
    title: "创意",
    description: "发现创作工具、模板与可安装的能力。",
    icon: Sparkles,
    path: "/creative-workshop",
    links: [["浏览创意", "/creative-workshop"]],
  },
  {
    title: "集成",
    description: "管理工具 API、MCP、Skill 与外部连接。",
    icon: Link2,
    path: "/integrations?kind=tool-apis",
    links: [
      ["工具 API", "/integrations?kind=tool-apis"],
      ["MCP", "/integrations?kind=mcp"],
      ["Skill", "/integrations?kind=skills"],
      ["外部连接", "/integrations?kind=external-connections"],
    ],
  },
  {
    title: "AI 能力",
    description: "配置模型和编程智能体，测试创作效果。",
    icon: Box,
    path: "/model-service",
    links: [
      ["模型服务", "/model-service"],
      ["编程智能体", "/code-agents"],
      ["模型测试", "/model-test"],
      ["调用日志", "/model-call-logs"],
    ],
  },
] as const;
export function ProductHome({
  navigate,
}: {
  navigate: (path: string) => void;
}) {
  return (
    <section className="page home-page product-home">
      <header className="home-header">
        <div className="home-header-copy">
          <span className="home-eyebrow">你的灵感，随时开场</span>
          <h1>{coreProduct()!.name}</h1>
          <p>从一个好想法，到下一件作品。</p>
        </div>
        <HomeHeroPet />
      </header>
      <div className="product-home-areas">
        {areas.map((area) => (
          <section key={area.title} className="home-panel product-home-area">
            <area.icon aria-hidden />
            <h2>
              <button onClick={() => navigate(area.path)}>
                {area.title}
                <ArrowUpRight size={18} />
              </button>
            </h2>
            <p>{area.description}</p>
            <nav aria-label={area.title}>
              {area.links.map(([title, path]) => (
                <button key={path} onClick={() => navigate(path)}>
                  {title}
                </button>
              ))}
            </nav>
          </section>
        ))}
      </div>
    </section>
  );
}
