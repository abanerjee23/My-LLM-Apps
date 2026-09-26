import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const signOut = vi.fn(async () => {});
let googleCallback: (response: {credential?: string}) => void = () => {};

import App from "./App";
import { AuthContext, AuthProvider, type SessionUser } from "./auth";

const user: SessionUser = { user_id: "user-1", displayName: "Ada Lovelace", email: "ada@example.com", photoURL: null };
type FakeResponse = { ok: boolean; status: number; json: () => Promise<unknown> };
const okResponse = async (url: string): Promise<FakeResponse> => ({ ok: true, status: 200, json: async () => url.includes("requirements") ? {service_account: "reader@project.iam.gserviceaccount.com"} : [] });
const fetchMock = vi.fn(okResponse);
vi.stubGlobal("fetch", fetchMock);

function renderSignedIn() {
  return render(<AuthContext.Provider value={{ user, signOut }}><App /></AuthContext.Provider>);
}

beforeEach(() => {
  fetchMock.mockReset(); fetchMock.mockImplementation(okResponse); signOut.mockClear(); window.history.replaceState({}, "", "/");
  window.google = {accounts:{id:{
    initialize: ({callback}) => { googleCallback = callback; },
    renderButton: (element) => { const button = document.createElement("button"); button.textContent = "Continue with Google"; button.onclick = () => googleCallback({credential:"fresh-token"}); element.appendChild(button); },
    disableAutoSelect: vi.fn(),
  }}};
});
afterEach(cleanup);

test("navigation separates source setup from the home composer", async () => {
  renderSignedIn();
  expect(screen.getByLabelText("Investigation brief")).toBeTruthy();
  expect(screen.queryByText("Upload a file")).toBeNull();
  fireEvent.click(screen.getByRole("link", { name: "Sources" }));
  await waitFor(() => expect(window.location.pathname).toBe("/sources"));
  fireEvent.click(screen.getByRole("button", { name: /Connect BigQuery/ }));
  expect(screen.getByLabelText(/^Google Cloud project ID/)).toBeTruthy();
  expect(screen.getByLabelText(/^Dataset location/)).toBeTruthy();
  expect(screen.getByRole("button", {name: "Verify access"})).toBeDisabled();
  expect(screen.getByText("BigQuery Data Viewer")).toBeTruthy();
  expect(screen.getByText("BigQuery Job User")).toBeTruthy();
  expect(screen.queryByLabelText("Investigation brief")).toBeNull();
  fireEvent.click(screen.getByRole("link", {name: "Notebook"}));
  expect(screen.getByText("A home for your conclusions")).toBeTruthy();
});

test("API calls use same-origin server sessions instead of browser bearer tokens", async () => {
  renderSignedIn();
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  for (const [, options] of fetchMock.mock.calls as unknown as [string, RequestInit][]) {
    expect(options.credentials).toBe("same-origin");
    expect((options.headers as Record<string, string>).Authorization).toBeUndefined();
  }
});

test("signed-in person is shown with a sign-out menu", async () => {
  renderSignedIn();
  fireEvent.click(screen.getByRole("button", { name: /Ada Lovelace/ }));
  expect(screen.getByText("ada@example.com")).toBeTruthy();
  fireEvent.click(screen.getByRole("menuitem", { name: "Sign out" }));
  expect(signOut).toHaveBeenCalled();
});

test("an expired session raises the application auth event", async () => {
  const expired = vi.fn();
  window.addEventListener("sourcelens:unauthorized", expired);
  fetchMock.mockImplementation(async (): Promise<FakeResponse> => ({ ok: false, status: 401, json: async () => ({ detail: "Your session has expired. Sign in again." }) }));
  renderSignedIn();
  await waitFor(() => expect(expired).toHaveBeenCalled());
  window.removeEventListener("sourcelens:unauthorized", expired);
});

test("without a session only the sign-in page is shown", async () => {
  fetchMock.mockImplementation(async (): Promise<FakeResponse> => ({ ok: false, status: 401, json: async () => ({ detail: "Sign in" }) }));
  render(<AuthProvider><App /></AuthProvider>);
  expect(await screen.findByRole("button", { name: "Continue with Google" })).toBeTruthy();
  expect(screen.queryByLabelText("Investigation brief")).toBeNull();
  expect(fetchMock).toHaveBeenCalledWith("/api/me", expect.objectContaining({ credentials: "same-origin" }));
});

test("Google login confirms the server cookie before showing private data", async () => {
  let authenticated = false;
  fetchMock.mockImplementation(async (url: string): Promise<FakeResponse> => {
    if (url === "/api/auth/session") authenticated = true;
    if (url === "/api/me") return {ok:authenticated,status:authenticated ? 200 : 401,json:async () => ({user_id:"1",email:"ada@example.com",display_name:"Ada Lovelace"})};
    return {ok:true,status:200,json:async () => []};
  });
  render(<AuthProvider><App/></AuthProvider>);
  fireEvent.click(await screen.findByRole("button", {name:"Continue with Google"}));
  expect(await screen.findByRole("button", {name:/Ada Lovelace/})).toBeTruthy();
  expect(fetchMock.mock.calls.filter(([url]) => url === "/api/me")).toHaveLength(2);
});

test("a rejected Google credential shows the server error", async () => {
  fetchMock.mockImplementation(async (url: string): Promise<FakeResponse> => url === "/api/auth/session"
    ? {ok:false,status:403,json:async()=>({detail:"This Google account does not have access to SourceLens."})}
    : {ok:false,status:401,json:async()=>({})});
  render(<AuthProvider><App/></AuthProvider>);
  fireEvent.click(await screen.findByRole("button",{name:"Continue with Google"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("does not have access");
});
