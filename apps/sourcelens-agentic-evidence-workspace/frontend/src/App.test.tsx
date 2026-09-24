import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import App from "./App";

vi.stubGlobal("fetch", vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes("requirements") ? {service_account: "reader@project.iam.gserviceaccount.com"} : [] })));

test("navigation separates source setup from the home composer", async () => {
  window.history.replaceState({}, "", "/");
  render(<App />);
  expect(screen.getByLabelText("Investigation brief")).toBeTruthy();
  expect(screen.queryByText("Upload a file")).toBeNull();
  fireEvent.click(screen.getByRole("link", { name: "Sources" }));
  await waitFor(() => expect(window.location.pathname).toBe("/sources"));
  fireEvent.click(screen.getByRole("button", { name: /Connect BigQuery/ }));
  expect(screen.getByLabelText("Project ID")).toBeTruthy();
  expect(screen.getByLabelText("Dataset location")).toBeTruthy();
  expect(screen.getByRole("button", {name: "Verify connection"})).toBeDisabled();
  expect(screen.queryByLabelText("Investigation brief")).toBeNull();
  fireEvent.click(screen.getByRole("link", {name: "Notebook"}));
  expect(screen.getByText("A home for your conclusions")).toBeTruthy();
});
