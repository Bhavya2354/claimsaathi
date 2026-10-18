import { StartCase } from "@/components/StartCase";
import styles from "./home.module.css";

const STEPS = [
  {
    title: "Upload what you have",
    body: "Your policy wording, the hospital bill or estimate, and the insurer's letter if the claim was cut. Photos of paper documents work too.",
  },
  {
    title: "See every deduction",
    body: "Each bill line is marked covered, partly covered or not covered, with the rupee amount and the policy clause behind it.",
  },
  {
    title: "Push back with evidence",
    body: "If the insurer cut more than the policy allows, get a grievance letter or an Ombudsman complaint that cites the exact clauses.",
  },
];

export default function Home() {
  return (
    <div className={styles.home}>
      <section className={styles.hero}>
        <h1>Know what your health insurance should pay, before you argue about it.</h1>
        <p className={styles.lede}>
          Room rent limits, proportionate deductions, co-payments, non-payable items and waiting
          periods decide most claim cuts. ClaimSaathi reads your policy and your bill and works out
          each one.
        </p>
        <StartCase />
        <p className={styles.privacy}>
          Names, phone, Aadhaar, PAN and policy numbers are masked before any AI model sees the
          text. Original files are never stored, and the case is deleted after 24 hours.
        </p>
      </section>

      <ol className={styles.steps}>
        {STEPS.map((step, index) => (
          <li key={step.title}>
            <span className={`num ${styles.index}`}>{String(index + 1).padStart(2, "0")}</span>
            <h3>{step.title}</h3>
            <p>{step.body}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
