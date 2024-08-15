import sqlite3 as sl
import re
import glob

class CSVFieldIndexes:
    ABC_DEF: int = 0
    FROM_NUMBER: int = 1
    TO_NUMBER: int = 2
    AMOUNT: int = 3
    OPERATOR: int = 4 
    REGION: int = 5
    TERRITORIYA_GAR: int = 6
    INN: int = 7

class DbFieldIndexes:
    ABC_DEF: int = 0
    NUMBER: int = 1
    FULL_NUMBER: int = 2
    FROM_NUMBER: int = 3
    TO_NUMBER: int = 4
    AMOUNT: int = 5
    OPERATOR: int = 6 
    REGION: int = 7
    TERRITORIYA_GAR: int = 8
    INN: int = 9

def parse_string(input_string):
    # Регулярное выражение для поиска 8ми слов, разделённых ";"
    pattern = r'^\s*(.+)\s*;\s*(.+)\s*;\s*(.+)\s*;\s*(.+)\s*;\s*(.+)\s*;\s*(.+)\s*;\s*(.+)\s*;\s*(.+)\s*$'
    
    match = re.match(pattern, input_string)
    if match:
        word1 = match.group(1)
        word2 = match.group(2)
        word3 = match.group(3)
        word4 = match.group(4)
        word5 = match.group(5)
        word6 = match.group(6)
        word7 = match.group(7)
        word8 = match.group(8)
        return word1, word2, word3, word4, word5, word6, word7, word8
    else:
        return None

def expand_numbers_pool (in_result):
    sql_data = []
    current_num = int(in_result[CSVFieldIndexes.FROM_NUMBER])
    from_num = int(in_result[CSVFieldIndexes.FROM_NUMBER])
    to_num = int(in_result[CSVFieldIndexes.TO_NUMBER])+1
    for current_num in range(from_num, to_num):
        sql_data.append(
           (in_result[CSVFieldIndexes.ABC_DEF], current_num, f"7{in_result[CSVFieldIndexes.ABC_DEF]}{current_num:0>7}", in_result[CSVFieldIndexes.FROM_NUMBER], 
           in_result[CSVFieldIndexes.TO_NUMBER], in_result[CSVFieldIndexes.AMOUNT], in_result[CSVFieldIndexes.OPERATOR],
           in_result[CSVFieldIndexes.REGION], in_result[CSVFieldIndexes.TERRITORIYA_GAR], in_result[CSVFieldIndexes.INN])
        )
        current_num += 1
    return sql_data
    
# открываем файл с базой данных
con = sl.connect('db.sqlite/numeration_registry.db')

# открываем базу
# создаём таблицу для реестра плана нурации
# АВС/ DEF;От;До;Емкость;Оператор;Регион;Территория ГАР;ИНН
# 499;9999701;9999999;299;АО "ГЛОБУС-ТЕЛЕКОМ";г. Москва;Город Москва;7715227394
con.execute("""
    CREATE TABLE IF NOT EXISTS numbers (
    abc_def INTEGER,    
    number INTEGER,
    fullnumber INTEGER PRIMARY KEY,
    from_number INTEGER,
    to_number INTEGER,        
    amount INTEGER,
    operator TEXT,
    region TEXT,
    territoriya_gar TEXT,
    inn TEXT                        
    );
""")
csv_files = sorted(glob.glob("./inCSV/*.csv"))
i=0
for csvf in csv_files:
    with open(csvf, 'r', encoding='utf-8') as f:
        for line in f:
            if i>0 :     
                result = parse_string(line) 
                if result:
                    print(f"{csvf} АВС/DEF: {result[CSVFieldIndexes.ABC_DEF]} От: {result[CSVFieldIndexes.FROM_NUMBER]} До: {result[CSVFieldIndexes.TO_NUMBER]}")
                    # подготавливаем  запрос
                    sql = 'INSERT INTO numbers (abc_def, number, fullnumber, from_number, to_number, amount, operator, region, territoriya_gar, inn) values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
                    data = []
                    data = expand_numbers_pool (result)
                    # указываем данные для запроса
                    #data = [
                    #    (result[CSVFieldIndexes.ABC_DEF], 0000000+i, 70000000000+i, result[CSVFieldIndexes.FROM_NUMBER], 
                    #    result[CSVFieldIndexes.TO_NUMBER], result[CSVFieldIndexes.AMOUNT], result[CSVFieldIndexes.OPERATOR],
                    #      result[CSVFieldIndexes.REGION], result[CSVFieldIndexes.TERRITORIYA_GAR], result[CSVFieldIndexes.INN])
                    # data.append(('1','2','3'))
                    # добавляем запись в таблицу
                    with con:
                     con.executemany(sql, data)
                     con.commit()    
                else:
                    print("Строка не соответствует формату.")
            i=i+1
f.close()
con.close()

