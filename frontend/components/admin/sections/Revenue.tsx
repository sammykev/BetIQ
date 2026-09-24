"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Banknote, Receipt } from "lucide-react";
import { Btn, Card, Skeleton, Stat, naira, num, useAdmin } from "../ui";
import { ColumnChart } from "../charts";

export function RevenueSection() {
  const { get, adminFetch } = useAdmin();
  const [revenue, setRevenue] = useState<any>(null);
  const [subs, setSubs] = useState<{ total: number; premium: number } | null>(null);
  const [users, setUsers] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const load = async (fresh = false) => {
    setLoading(true);
    setRevenue(await get(`/api/admin/revenue${fresh ? "?fresh=true" : ""}`));
    setLoading(false);
  };
  useEffect(() => {
    load();
    adminFetch("/api/admin/subscribers").then(r => r.json()).then(setSubs).catch(() => {});
    adminFetch("/api/admin/users").then(r => r.json()).then(setUsers).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-4">
      {!revenue ? <Card><Skeleton rows={4} /></Card> : revenue.error ? (
        <Card title="Revenue" icon={<Banknote size={15} />}>
          <p className="text-sm text-warn flex items-center gap-2"><AlertTriangle size={14} />
            {revenue.error === "no_paystack_key" ? "PAYSTACK_SECRET_KEY isn't set on Render." : revenue.error}</p>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
            <Stat label="Today" value={naira(revenue.today)} tone="accent" />
            <Stat label="Last 7 days" value={naira(revenue.last_7_days)} />
            <Stat label="Last 30 days" value={naira(revenue.last_30_days)} />
            <Stat label="All time" value={naira(revenue.total_revenue)} sub={`${num(revenue.transaction_count)} payments`} />
            <Stat label="Paying customers" value={num(revenue.customers)} />
            <Stat label="Average payment" value={naira(revenue.average)} />
          </div>
          <Card title="Revenue by day" icon={<Banknote size={15} />} subtitle="Last 30 days, successful Paystack payments"
            action={<Btn onClick={() => load(true)} busy={loading}>Refresh</Btn>}>
            <ColumnChart name="Revenue by day" labels={revenue.series.map((s: any) => s.date.slice(5))}
              values={revenue.series.map((s: any) => s.amount)} format={naira} tick={5} />
          </Card>
        </>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Subscribers" icon={<Receipt size={15} />}>
          {!subs ? <Skeleton rows={2} /> : (
            <div className="grid grid-cols-3 gap-3">
              <Stat label="Users" value={num(subs.total)} />
              <Stat label="Premium" value={num(subs.premium)} tone="info" />
              <Stat label="Conversion" value={`${subs.total ? Math.round((subs.premium / subs.total) * 100) : 0}%`} />
            </div>
          )}
          {users?.expiring?.length > 0 && (
            <div className="space-y-1.5">
              <p className="eyebrow">Premium ending within 7 days — worth a reminder</p>
              {users.expiring.map((u: any) => (
                <div key={u.id} className="flex items-center justify-between text-xs">
                  <span className="text-n-200 truncate">{u.email}</span>
                  <span className="text-warn font-semibold ml-2">{u.days_left}d left</span>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Recent payments" icon={<Receipt size={15} />}>
          {!revenue?.recent?.length ? <p className="text-xs text-n-400">No payments yet.</p> : (
            <ul className="divide-y divide-n-800 text-xs">
              {revenue.recent.map((t: any) => (
                <li key={t.reference} className="flex items-center gap-2 py-1.5">
                  <span className="text-n-200 truncate flex-1">{t.email}</span>
                  <span className="text-n-500">{t.channel}</span>
                  <span className="text-accent font-semibold tnum">{naira(t.amount)}</span>
                  <span className="text-n-500 tnum w-20 text-right">{t.date}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
