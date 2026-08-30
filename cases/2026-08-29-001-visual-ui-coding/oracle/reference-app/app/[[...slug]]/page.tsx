import {TracegridApp} from "../tracegrid-app";

type PageProps = {
  params: Promise<{slug?: string[]}>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export default async function Page({params, searchParams}: PageProps) {
  const [{slug = []}, query] = await Promise.all([params, searchParams]);
  const view = slug[0] === "runs" ? (slug[1] ? "detail" : "runs") : "overview";

  return (
    <TracegridApp
      initialView={view}
      filterOpen={query.filter === "open"}
      menuOpen={query.menu === "open"}
    />
  );
}
