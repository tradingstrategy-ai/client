"""Compare pyarrow and fastparquet files.

- Use the sample Parquet file (54MB): https://drive.google.com/file/d/1LtiL-n50cNx1JMBC0QE-Ztvf8ENSb-OE/view?usp=sharing
  - save as "/tmp/candles-30d.parquet"

- We found out that rows and the contents of pair_id column is corrupted
  (among other corruption if you do row by row comparison, but this is easy to spot)]

- One potential cause is usage of row groups in the file

- The file has been written with pyarrow / Python


"""
from pyarrow import parquet as pq  # pyarrow 10
from fastparquet import ParquetFile  # fastparquet  2023.1.0

path = "/tmp/candles-30d.parquet"

pf1 = ParquetFile(path)
pf2 = pq.read_table(path)

df1 = pf1.to_pandas()
df2 = pf2.to_pandas()

print("Rows ", len(df2))
assert len(df1) == len(df2)  # Passes, looks like row count matches

# fastparquet only sees 1280 pair_ids out of 150903
print("Unique pairs fastparquet", len(df1.pair_id.unique()))
print("Unique pairs pyarrow", len(df2.pair_id.unique()))
assert len(df1.pair_id.unique()) == len(df2.pair_id.unique())