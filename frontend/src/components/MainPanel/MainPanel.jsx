import TopBar from "./TopBar";
import Messages from "./Messages";
import InputArea from "./InputArea";

export default function MainPanel() {
  return (
    <div className="main">
      <TopBar />
      <Messages />
      <InputArea />
    </div>
  );
}
