import { Header } from "./Header";
import { Footer } from "./Footer";
import { Conversation } from "../features/conversation/Conversation";
import { SettingsDialog } from "../features/settings/SettingsDialog";
import { DownloadsDialog } from "../features/downloads/DownloadsDialog";
export function App() {
  return (
    <>
      <div id="app">
        <Header />
        <Conversation />
        <Footer />
      </div>
      <SettingsDialog />
      <DownloadsDialog />
    </>
  );
}
