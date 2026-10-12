import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";

import { ProfileProvider } from "../lib/profile";

export function renderPage(ui: ReactElement, route = "/") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ProfileProvider>
        <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
      </ProfileProvider>
    </QueryClientProvider>,
  );
}
