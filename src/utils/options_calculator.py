import pandas as pd

class OptionsCalculator:
    """Utility class for parsing and calculating metrics from NSE Option Chain data."""

    def __init__(self, logger=None):
        from src.utils.logger_config import get_logger
        self.logger = logger or get_logger("OptionsCalculator")

    def calculate_max_pain(self, df: pd.DataFrame) -> float:
        """Calculates Option Max Pain based on the standard Open Interest loss formula."""
        min_loss = float("inf")
        max_pain = None
        for expected_expiry in df["Strike Price"].unique():
            ce_loss = ((expected_expiry - df["Strike Price"]).clip(lower=0) * df["CE_OI"]).sum()
            pe_loss = ((df["Strike Price"] - expected_expiry).clip(lower=0) * df["PE_OI"]).sum()
            total_loss = ce_loss + pe_loss
            if total_loss < min_loss:
                min_loss = total_loss
                max_pain = expected_expiry
        return max_pain

    def process_chain_data(self, json_data: dict, spot_price: float) -> str:
        """
        Parses the JSON Option Chain to identify current Spot Price, locate ATM strike, 
        filter to ATM +/- 10 strikes, and format text strings.
        """
        if not json_data or "filtered" not in json_data or "data" not in json_data["filtered"]:
            raise ValueError("Invalid JSON data provided.")
            
        records = json_data["filtered"]["data"]
        timestamp = json_data.get("records", {}).get("timestamp", "Unknown")
        self.logger.info("Processing NSE data with timestamp: %s", timestamp)
        
        # Flatten the JSON to a DataFrame
        rows = []
        for r in records:
            row = {"Strike Price": r.get("strikePrice")}
            if "CE" in r:
                row["CE_OI"] = r["CE"].get("openInterest", 0)
                row["CE_COI"] = r["CE"].get("changeInOpenInterest", 0)
                row["CE_LTP"] = r["CE"].get("lastPrice", 0)
                row["CE_Ask"] = r["CE"].get("sellPrice1", 0)
            if "PE" in r:
                row["PE_OI"] = r["PE"].get("openInterest", 0)
                row["PE_COI"] = r["PE"].get("changeInOpenInterest", 0)
                row["PE_LTP"] = r["PE"].get("lastPrice", 0)
                row["PE_Ask"] = r["PE"].get("sellPrice1", 0)
            rows.append(row)
            
        df = pd.DataFrame(rows)
        df.dropna(subset=["Strike Price"], inplace=True)
        df.sort_values("Strike Price", inplace=True)
        df.reset_index(drop=True, inplace=True)
        
        # Fallback if spot wasn't found
        if not spot_price:
            self.logger.warning("Calculating fallback spot price by finding min CE/PE LTP difference...")
            df["LTP_diff"] = abs(df.get("CE_LTP", 0) - df.get("PE_LTP", 0))
            atm_row = df.loc[df["LTP_diff"].idxmin()]
            spot_price = float(atm_row["Strike Price"])
            
        # 1. Locate ATM Strike
        df["Strike_Diff"] = abs(df["Strike Price"] - spot_price)
        atm_idx = df["Strike_Diff"].idxmin()
        atm_strike = df.loc[atm_idx, "Strike Price"]
        
        # 2. Calculate Market Internals
        total_ce_oi = df["CE_OI"].sum()
        total_pe_oi = df["PE_OI"].sum()
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0.0
        max_pain = self.calculate_max_pain(df)
        
        # 3. Filter ATM +/- 10 Strikes
        start_idx = max(0, atm_idx - 10)
        end_idx = min(len(df), atm_idx + 11)
        filtered_df = df.iloc[start_idx:end_idx].copy()
        
        # 4. Format Output Text
        summary = [
            f"--- LIVE MARKET DATA ---",
            f"NSE Data Timestamp: {timestamp}",
            f"Current Spot Price: {spot_price}",
            f"ATM Strike: {atm_strike}",
            f"Estimated Max Pain: {max_pain}",
            f"Overall PCR (OI based): {pcr}",
            f"\nLive Option Premiums (ATM +/- 10 Strikes):",
            f"Format -> STRIKE | CE LTP (Ask) | PE LTP (Ask) | CE OI (Change) | PE OI (Change)"
        ]
        
        atm_rel_idx = filtered_df.index.get_loc(atm_idx)
        
        for i, row in filtered_df.iterrows():
            strike = row["Strike Price"]
            ce_ltp = row.get("CE_LTP", "-")
            ce_ask = row.get("CE_Ask", "-")
            pe_ltp = row.get("PE_LTP", "-")
            pe_ask = row.get("PE_Ask", "-")
            
            # Redact expensive premiums to force LLM constraint
            MAX_PREMIUM = 180.0
            if isinstance(ce_ltp, (int, float)) and ce_ltp > MAX_PREMIUM:
                ce_ltp = ">180 (INVALID)"
                ce_ask = ">180"
            if isinstance(pe_ltp, (int, float)) and pe_ltp > MAX_PREMIUM:
                pe_ltp = ">180 (INVALID)"
                pe_ask = ">180"
                
            ce_oi = row.get("CE_OI", "-")
            ce_coi = row.get("CE_COI", 0)
            pe_oi = row.get("PE_OI", "-")
            pe_coi = row.get("PE_COI", 0)
            
            # Format COI with +/- sign
            ce_coi_str = f"{ce_coi:+}" if isinstance(ce_coi, (int, float)) else str(ce_coi)
            pe_coi_str = f"{pe_coi:+}" if isinstance(pe_coi, (int, float)) else str(pe_coi)
            
            # Label Key Strikes
            current_rel_idx = filtered_df.index.get_loc(i)
            tag = ""
            if current_rel_idx == atm_rel_idx:
                tag = " <-- [ATM]"
            elif current_rel_idx == atm_rel_idx - 1:
                tag = " <-- [CE ITM1 / PE OTM1]"
            elif current_rel_idx == atm_rel_idx + 1:
                tag = " <-- [CE OTM1 / PE ITM1]"
                
            line = f"{strike} | CE: {ce_ltp} ({ce_ask}) | PE: {pe_ltp} ({pe_ask}) | CE_OI: {ce_oi} ({ce_coi_str}) | PE_OI: {pe_oi} ({pe_coi_str}){tag}"
            summary.append(line)
            
        return "\n".join(summary)
