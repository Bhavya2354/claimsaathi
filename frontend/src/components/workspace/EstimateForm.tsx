"use client";

import { useState } from "react";

import type { LineCategory } from "@/lib/types";
import styles from "./workspace.module.css";

interface Row {
  description: string;
  category: LineCategory;
  amount: string;
}

const PRESET: Row[] = [
  { description: "Surgery or package charges", category: "surgery", amount: "" },
  { description: "Doctor and surgeon fees", category: "doctor_fees", amount: "" },
  { description: "Medicines", category: "pharmacy", amount: "" },
  { description: "Tests and scans", category: "investigations", amount: "" },
  { description: "Implants (lens, stent, plates)", category: "implants", amount: "" },
  { description: "Nursing", category: "nursing", amount: "" },
];

export type EstimateLines = {
  description: string;
  amount: string;
  category: LineCategory;
  quantity?: string;
}[];

export function EstimateForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (lines: EstimateLines) => void;
}) {
  const [rent, setRent] = useState("");
  const [days, setDays] = useState("");
  const [rows, setRows] = useState<Row[]>(PRESET);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const lines: EstimateLines = rows
      .filter((row) => Number(row.amount) > 0)
      .map(({ description, category, amount }) => ({ description, category, amount }));
    if (Number(rent) > 0 && Number(days) > 0) {
      lines.unshift({
        description: "Room rent",
        category: "room",
        amount: String(Number(rent) * Number(days)),
        quantity: days,
      });
    }
    if (lines.length > 0) onSubmit(lines);
  }

  return (
    <form className={`panel ${styles.section} ${styles.card}`} onSubmit={submit}>
      <p className={styles.caption}>
        Enter what the hospital expects to charge. Leave a row empty if it does not apply.
      </p>
      <div className={styles.estimate}>
        <div className="field">
          <label htmlFor="rent">Room rent per day (₹)</label>
          <input
            id="rent"
            className="input"
            type="number"
            min={0}
            value={rent}
            onChange={(e) => setRent(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="days">Days</label>
          <input
            id="days"
            className="input"
            type="number"
            min={1}
            value={days}
            onChange={(e) => setDays(e.target.value)}
          />
        </div>
        <span />
        {rows.map((row, i) => (
          <div key={row.description} className={`field ${styles.full}`}>
            <label htmlFor={`row-${i}`}>{row.description} (₹)</label>
            <input
              id={`row-${i}`}
              className="input"
              type="number"
              min={0}
              value={row.amount}
              onChange={(e) =>
                setRows((all) =>
                  all.map((r, j) => (j === i ? { ...r, amount: e.target.value } : r)),
                )
              }
            />
          </div>
        ))}
      </div>
      <div className={styles.actions}>
        <button type="submit" className="button" disabled={disabled}>
          Estimate what I would pay
        </button>
      </div>
    </form>
  );
}
