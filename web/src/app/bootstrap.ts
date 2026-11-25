import { startBootLoading } from "./bootLoading";

// Keep refresh feedback independent of React and feature downloads.
const stopBootLoading = startBootLoading();
void import("../main").then(stopBootLoading).catch(() => {
  stopBootLoading();
  const panel = document.createElement("section");
  panel.setAttribute("role", "alert");
  const message = document.createElement("p");
  message.textContent = "页面资源加载失败，请检查网络后重新加载。";
  const retry = document.createElement("button");
  retry.textContent = "重新加载";
  retry.onclick = () => window.location.reload();
  panel.append(message, retry);
  document.getElementById("root")!.replaceChildren(panel);
});
