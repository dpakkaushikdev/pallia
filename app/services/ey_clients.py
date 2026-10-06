"""EY seller and buyer details from the supplied client GST workbook."""
from decimal import Decimal

from app.services.eee_taxi_clients import ClientRecord, UnknownClientError

SELLER_GSTIN = "06AANCA3858Q1ZW"
SELLER_STATE = "Haryana"
SELLER_ADDRESS = "7th Floor, Unit Nos. 701-705, Good Earth Business Bay-I, Sector-58, Gurugram, Haryana, 122098"

# Active buyer registrations supplied in Updated GST details with address new.xlsx.
# Keep one record per GSTIN; billing state is derived from its first two digits.
EY_CLIENTS = {
    '06AAEFE1763C1ZW': ClientRecord(entity_name='Ernst & Young LLP', address='Ground Floor, Plot No.67, Sector 44, Institutional Area, Gurugram, Gurugram, Haryana, 122003', gstin='06AAEFE1763C1ZW'),
    '07AAEFE1763C1ZU': ClientRecord(entity_name='Ernst & Young LLP', address='3rd and 6th Floor, Worldmark 1, Asset Area 11,Hospitality District, New Delhi, New Delhi, Delhi, 110037', gstin='07AAEFE1763C1ZU'),
    '27AAEFE1763C1ZS': ClientRecord(entity_name='Ernst & Young LLP', address='6th, 14th, 15th, 16th, 17th, Plot No.29, The Ruby, Senapati Bapat Marg, Dadar West, Mumbai, Mumbai, Maharashtra, 400028', gstin='27AAEFE1763C1ZS'),
    '36AAEFE1763C1ZT': ClientRecord(entity_name='Ernst & Young LLP', address='18th Floor, THE SKYVIEW 10, SOUTH LOBBY,, Survey No 83/1, Raidurgam, Hyderabad, Hyderabad, Telangana, 500032', gstin='36AAEFE1763C1ZT'),
    '04AAEFE1763C1Z0': ClientRecord(entity_name='Ernst & Young LLP', address='6th Floor, Elante offices, Unit No. B-613 and 614, Plot No- 178-178A, Industrial and Business Park, Phase-I, Chandigarh, 160002', gstin='04AAEFE1763C1Z0'),
    '05AAEFE1763C1ZY': ClientRecord(entity_name='Ernst & Young LLP', address='House No. 33, First Floor, Subhash Road, Subhash Road, Dehradun, Uttarakhand, 248001', gstin='05AAEFE1763C1ZY'),
    '19AAEFE1763C1ZP': ClientRecord(entity_name='Ernst & Young LLP', address="22, Camac Street 3rd Floor, Block 'C', Kolkata-700 016, India", gstin='19AAEFE1763C1ZP'),
    '20AAEFE1763C1Z6': ClientRecord(entity_name='Ernst & Young LLP', address='first floor rear side, Fairdeal Complex, Holding No 7 S.B Shop Area, P.S. Bistupur, Jamshedpur, East Singhbhum, Jharkhand, 831001', gstin='20AAEFE1763C1Z6'),
    '09AAEFE1763C1ZQ': ClientRecord(entity_name='Ernst & Young LLP', address='4th & 5th Floor, Plot No. 2B, Tower 2, Sector 126, Noida-201304 , Gautam Budh Nagar, U.P. India', gstin='09AAEFE1763C1ZQ'),
    '33AAEFE1763C2ZY': ClientRecord(entity_name='Ernst & Young LLP', address='601, 701,702, 6 and 7 Floor, A Block, Tidel Park, Situated at No. 4,Rajiv Gandhi Salai,, Taramani,, Chennai, Chennai, Tamil Nadu, 600113', gstin='33AAEFE1763C2ZY'),
    '29AAEFE1763C2ZN': ClientRecord(entity_name='Ernst & Young LLP', address='Canberra block, 12th and 13th floor, No. 24, Vittal Mallya Road, UB City, Corporate division no-61, Bangalore, Bengaluru (Bangalore) Urban, Karnataka, 560001', gstin='29AAEFE1763C2ZN'),
    '32AAEFE1763C3ZZ': ClientRecord(entity_name='Ernst & Young LLP', address='9th Floor, Abad nucleus, Madurai NH 49, Off Kundannor Junction, Kanauannur Taluk, Maradu, Ernakulam, Kerala, 682304', gstin='32AAEFE1763C3ZZ'),
    '24AAEFE1763C1ZY': ClientRecord(entity_name='Ernst & Young LLP', address='B wing, 22nd floor, Privilon, Ambli BRT Road, Behind Iskcon temple, Off SG Highway, Ahmedabad, Gujarat-380059, India', gstin='24AAEFE1763C1ZY'),
    '08AAEFE1763C1ZS': ClientRecord(entity_name='Ernst & Young LLP', address='9th floor, Jewel of India, Horizon Tower, JLN Marg, Opp Jaipur Stock Exchange, Jaipur, Jaipur, Rajasthan, 302018', gstin='08AAEFE1763C1ZS'),
    '21AAEFE1763C1Z4': ClientRecord(entity_name='Ernst & Young LLP', address='8th Floor, Odhisha start up incubation centre O-Hub, Tower A, SEZ Road, chandaka,Bhubaneswar, Khordha, Odisha, 751024', gstin='21AAEFE1763C1Z4'),
    '23AAEFE1763C1Z0': ClientRecord(entity_name='Ernst & Young LLP', address='B3/603,Pacific Blue, Next to D-mart, Hoshangabad Road, Bhopal, Madhya Pradesh 462026', gstin='23AAEFE1763C1Z0'),
    '37AAEFE1763C2ZQ': ClientRecord(entity_name='Ernst & Young LLP', address='Prashantha Nilayam, Door No. 48, 19.4C, Sri Ramchandra Nagar Vijayawada, Vijayawada, Krishna, Andhra Pradesh, 520001', gstin='37AAEFE1763C2ZQ'),
    '27AAGFE3290E1ZK': ClientRecord(entity_name='EY Actuarial Services LLP', address='14th Floor, The Ruby 29, Senapati Bapat Marg, Dadar (West), Mumbai-400 028, India', gstin='27AAGFE3290E1ZK'),
    '19AAGFE3290E1ZH': ClientRecord(entity_name='EY Actuarial Services LLP', address="22, Camac Street 3rd Floor, Block 'C', Kolkata-700 016, India", gstin='19AAGFE3290E1ZH'),
    '06AAGFE3290E1ZO': ClientRecord(entity_name='EY Actuarial Services LLP', address='Ground Floor, Plot No.67, Sector 44, Institutional Area, Gurugram, Gurugram, Haryana, 122003', gstin='06AAGFE3290E1ZO'),
    '27AAGFE3290E2ZJ': ClientRecord(entity_name='EY Actuarial Services LLP', address='14th Floor, The Ruby 29, Senapati Bapat Marg, Dadar (West), Mumbai-400 028, India', gstin='27AAGFE3290E2ZJ'),
    '07AAGFE6873R1ZK': ClientRecord(entity_name='EY Restructuring LLP', address='3rd & 6th Floor, Worldmark-1, IGI Airport Hospitality District Aerocity, New Delhi- 110 037, India', gstin='07AAGFE6873R1ZK'),
    '27AAGFE6873R1ZI': ClientRecord(entity_name='EY Restructuring LLP', address='14th Floor, The Ruby 29, Senapati Bapat Marg, Dadar (West), Mumbai-400 028, India', gstin='27AAGFE6873R1ZI'),
    '36AAGFE6873R1ZJ': ClientRecord(entity_name='EY Restructuring LLP', address='THE SKYVIEW 10, "SOUTH LOBBY", 18th Floor, Survey No 83/1, Raidurgam, Hyderabad, Hyderabad, Telangana, 500032', gstin='36AAGFE6873R1ZJ'),
    '33AAGFE6873R1ZP': ClientRecord(entity_name='EY Restructuring LLP', address='6th & 7th Floor, "A" Block, Tidel Park, No.4, Rajiv Gandhi Salai, Taramani, Chennai-600 113, India', gstin='33AAGFE6873R1ZP'),
    '19AAGFE6873R1ZF': ClientRecord(entity_name='EY Restructuring LLP', address="22, Camac Street 3rd Floor, Block 'C', Kolkata-700 016, India", gstin='19AAGFE6873R1ZF'),
    '06AAEFE1778R1ZU': ClientRecord(entity_name='Ernst & Young Associates LLP', address='Ground Floor, Plot no 67, sector 44, Institutional Area, Gurugram, Gurugram, Haryana, 122003', gstin='06AAEFE1778R1ZU'),
    '07AAEFE1778R1ZS': ClientRecord(entity_name='Ernst & Young Associates LLP', address='6th Floor, Worldmark 1, Asset Area-11, Hospitality District, New Delhi, Delhi, 110037', gstin='07AAEFE1778R1ZS'),
    '27AAEFE1778R1ZQ': ClientRecord(entity_name='Ernst & Young Associates LLP', address='14th floor, Plot No. 29, Senapati Bapat Marg, Dadar West, Mumbai City, Maharashtra, 400028', gstin='27AAEFE1778R1ZQ'),
    '29AAEFE1778R1ZM': ClientRecord(entity_name='Ernst & Young Associates LLP', address='12th and 13th floor, Canberra block, No. 24, Vittal Mallya Road, UB City, Bengaluru, Bengaluru Urban, Karnataka, 560001', gstin='29AAEFE1778R1ZM'),
    '36AAEFE1778R1ZR': ClientRecord(entity_name='Ernst & Young Associates LLP', address='THE SKYVIEW 10, SOUTH LOBBY,, 18th Floor, Survey No 83/1, Raidurgam, Hyderabad, Hyderabad, Telangana, 500032', gstin='36AAEFE1778R1ZR'),
    '33AAEFE1778R1ZX': ClientRecord(entity_name='Ernst & Young Associates LLP', address='6th and 7th Floor, A Block, Tidel Park, Rajiv Gandhi Salai, Taranami, Chennai, Kanchipuram, Tamil Nadu, 600113', gstin='33AAEFE1778R1ZX'),
    '24AAEFE1778R1ZW': ClientRecord(entity_name='Ernst & Young Associates LLP', address='B wing, 22nd floor, Privilon, Ambli BRT Road, Behind Iskcon temple, Off SG Highway, Ahmedabad, Gujarat-380059, India', gstin='24AAEFE1778R1ZW'),
    '19AAEFE1778R1ZN': ClientRecord(entity_name='Ernst & Young Associates LLP', address="22, Camac Street 3rd Floor, Block 'C', Kolkata-700 016, India", gstin='19AAEFE1778R1ZN'),
    '04AAEFE1778R1ZY': ClientRecord(entity_name='Ernst & Young Associates LLP', address='6th floor, Unit no B-613, B-614, Plot no 178-178A, Elante offices, Industrial and business park phase 1, Chandigarh, Chandigarh, Chandigarh, 160002', gstin='04AAEFE1778R1ZY'),
    '06AAHFE5514C1ZW': ClientRecord(entity_name='Ernst & Young Merchant Banking Services LLP', address='Ground Floor, Plot No.67, Sector 44, Institutional Area, Gurugram, Gurugram, Haryana, 122003', gstin='06AAHFE5514C1ZW'),
    '07AAHFE5514C1ZU': ClientRecord(entity_name='Ernst & Young Merchant Banking Services LLP', address='Worldmark I, Asset Area 11, 3rd and 6th floor, Hospitality District, Aerocity, New Delhi, New Delhi, Delhi, 110037', gstin='07AAHFE5514C1ZU'),
    '27AAHFE5514C1ZS': ClientRecord(entity_name='Ernst & Young Merchant Banking Services LLP', address='14th floor, Plot no. 29, The Ruby, Senapati Bapat Marg, Dadar West, Mumbai City, Maharashtra, 400028', gstin='27AAHFE5514C1ZS'),
    '29AAHFE5514C1ZO': ClientRecord(entity_name='Ernst & Young Merchant Banking Services LLP', address='13th Floor, UB City, Vittal Mallya marg, Canberra Block. No 24, Bengaluru, Bengaluru (Bangalore) Urban, Karnataka, 560001', gstin='29AAHFE5514C1ZO'),
    '04AACCP8967E1Z9': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='Elante offices, Unit No. B-613 & 614, 6th Floor, Plot No- 178-178A, Industrial & Business Park, Phase-I, Chandigarh – 160002, India', gstin='04AACCP8967E1Z9'),
    '07AACCP8967E3Z1': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='3rd and 6th Floor, Worldmark 1, Asset Area-11, Hospitality District, Aerocity, New Delhi, Delhi, 110037', gstin='07AACCP8967E3Z1'),
    '24AACCP8967E3Z5': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='B wing, 22nd floor, Privilon, Ambli BRT Road, Behind Iskcon temple, Off SG Highway, Ahmedabad, Gujarat-380059, India', gstin='24AACCP8967E3Z5'),
    '06AACCP8967E1Z5': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='Plot No.67, Sector 44 Road, Gurugram, Gurugram, Haryana, 122003', gstin='06AACCP8967E1Z5'),
    '20AACCP8967E2ZE': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='First Floor Rear Side, Fairdeal Complex, Holding No 7 S.B Shop Area, P. S Bistupur, Jamshedpur, East Singhbhum, Jharkhand, 831001', gstin='20AACCP8967E2ZE'),
    '29AACCP8967E2ZW': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='UB City, Canberra,12th and 13th Floor,, No. 24, Vittal Mallya Road, Bangalore, Bengaluru (Bangalore) Urban, Karnataka, 560001', gstin='29AACCP8967E2ZW'),
    '32AACCP8967E1ZA': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='9th Floor, ABAD Nucleus, NH-49, Maradu PO, Kochi 682 304, Ernakulam, Kerala, 682304', gstin='32AACCP8967E1ZA'),
    '27AACCP8967E2Z0': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='The Ruby, 12th to 17th Floor, Plot No 29, Senapati Bapat Marg, Dadar West, Mumbai City, Maharashtra, 400028', gstin='27AACCP8967E2Z0'),
    '33AACCP8967E2Z7': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='Module No. 601, 701 and 702, 6th and 7th Floor, Tidel Park, Situted at No. 4, Rajiv Gandhi Salai, Taramani, Chennai, Tamil Nadu, 600113', gstin='33AACCP8967E2Z7'),
    '36AACCP8967E1Z2': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='THE SKYVIEW 10, SOUTH LOBBY,, 18th Floor, Survey No 83/1, Raidurgam, Hyderabad, Hyderabad, Telangana, 500032', gstin='36AACCP8967E1Z2'),
    '09AACCP8967E1ZZ': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='4th and 5th Floor, Plot No.2B, Tower II, Noida, Gautambuddha Nagar, Uttar Pradesh, 201304', gstin='09AACCP8967E1ZZ'),
    '19AACCP8967E2ZX': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='3rd Floor , 22, Camac Street, Block C, Kolkata, West Bengal, 700016', gstin='19AACCP8967E2ZX'),
    '08AACCP8967E2Z0': ClientRecord(entity_name='Ernst & Young Services Private Limited', address='9th Floor, Horizon Tower, Jewel of India, opp. Jaipur stock Exchange, JLN Marg, Jaipur, Jaipur, Rajasthan, 302018', gstin='08AACCP8967E2Z0'),
    '06ACHFS9117R1ZC': ClientRecord(entity_name='S R B C & CO LLP', address='6th Floor, Plot no. 67, Sector 44, Gurugram, Haryana,122003,India', gstin='06ACHFS9117R1ZC'),
    '27ACHFS9117R1Z8': ClientRecord(entity_name='S R B C & CO LLP', address='1st Floor, Block B7, Nirlon Knowledge Park, Western Express Highway, Next to Hub Mall, Goregaon East, Mumbai, Mumbai Suburban, Maharashtra, 400063', gstin='27ACHFS9117R1Z8'),
    '19ACHFS9117R1Z5': ClientRecord(entity_name='S R B C & CO LLP', address='3rd Floor, 22, Camac Street, Block B, Kolkata, West Bengal, 700016', gstin='19ACHFS9117R1Z5'),
    '09ACHFS9117R1Z6': ClientRecord(entity_name='S R B C & CO LLP', address='Tower 3, 7th floor, Plot No.2B, Sector 126, Noida, Gautam Buddha Nagar, Uttar Pradesh, 201304', gstin='09ACHFS9117R1Z6'),
    '24ACHFS9117R1ZE': ClientRecord(entity_name='S R B C & CO LLP', address='B wing,, 21st floor, Privilon, Ambli BRT Road, Behind Iskcon temple, Off SG Highway, Ahmedabad, Ahmedabad, Gujarat, 380059', gstin='24ACHFS9117R1ZE'),
    '06ACHFS9118A1ZA': ClientRecord(entity_name='S.R. Batliboi & Associates LLP', address='6th Floor, Plot No 67, Sector 44 Road, Institutional Area,Gurugram, Gurugram, Haryana, 122003', gstin='06ACHFS9118A1ZA'),
    '07ACHFS9118A1Z8': ClientRecord(entity_name='S.R. Batliboi & Associates LLP', address='4th Floor, Office 405 World Mark - 2, Asset No. 8 IGI Airport Hospitality District, Aerocity New Delhi-110037', gstin='07ACHFS9118A1Z8'),
    '27ACHFS9118A1Z6': ClientRecord(entity_name='S.R. Batliboi & Associates LLP', address='12th Floor, The Ruby, Plot No. 29 Senapati Bapat Marg Dadar (West) Mumbai 400 028, India', gstin='27ACHFS9118A1Z6'),
    '36ACHFS9118A1Z7': ClientRecord(entity_name='S.R. Batliboi & Associates LLP', address='THE SKYVIEW 10, "NORTH LOBBY", 18th Floor, Survey No 83/1, Raidurgam, Hyderabad, Hyderabad, Telangana, 500032', gstin='36ACHFS9118A1Z7'),
    '19ACHFS9118A1Z3': ClientRecord(entity_name='S.R. Batliboi & Associates LLP', address='22, 3rd Floor, Block B, Camac Street, Kolkata, Kolkata, West Bengal, 700016', gstin='19ACHFS9118A1Z3'),
    '09ACHFS9118A1Z4': ClientRecord(entity_name='S.R. Batliboi & Associates LLP', address='7th Floor, PLot No. 2B, Tower III, Sector 126, Noida, Gautambuddha Nagar, Uttar Pradesh, 201304', gstin='09ACHFS9118A1Z4'),
    '33ACHFS9118A1ZD': ClientRecord(entity_name='S.R. Batliboi & Associates LLP', address='Tidel Park, No. 4, 6th floor, A Block, Rajiv Gandhi Salai, Taramani, Chennai, Chennai, Tamil Nadu, 600113', gstin='33ACHFS9118A1ZD'),
    '29ACHFS9118A1Z2': ClientRecord(entity_name='S.R. Batliboi & Associates LLP', address='UB City Canberra block, 12th floor, No. 24, Vittal Mallya Road, Karnataka, Bengaluru (Bangalore) Urban, Karnataka, 560001', gstin='29ACHFS9118A1Z2'),
    '32ACHFS9118A1ZF': ClientRecord(entity_name='S.R. Batliboi & Associates LLP', address='9th Floor, Abad Nucleus, Madurai NH 49, Off Kundannor Junction, Kanauannur Taluk, Maradu, Ernakulam, Kerala, 682304', gstin='32ACHFS9118A1ZF'),
    '24ACHFS9118A1ZC': ClientRecord(entity_name='S.R. Batliboi & Associates LLP', address='21st Floor, B Wing, Privilon, Off SG Highway, Ambli BRT Road, Behind Iskcon Temple, Ahmedabad, Ahmedabad, Gujarat, 380059', gstin='24ACHFS9118A1ZC'),
    '08ACHFS9118A1Z6': ClientRecord(entity_name='S.R. Batliboi & Associates LLP', address='9th Floor, Horizon Tower, OppJaipur Stock Exchange, Jewelof India,JLN Marg, Jaipur, Jaipur, Rajasthan, 302018', gstin='08ACHFS9118A1Z6'),
    '27ACHFS9119B1Z3': ClientRecord(entity_name='S R B C & ASSOCIATES LLP', address='1st Floor, Block B7, Nirlon Knowledge Park, Western Express Highway, Next to Hub mall, Goregaon East, Mumbai, Mumbai Suburban, Maharashtra, 400063', gstin='27ACHFS9119B1Z3'),
    '19ACHFS9119B1Z0': ClientRecord(entity_name='S R B C & ASSOCIATES LLP', address="22, Camac Street 3rd Floor, Block 'C', Kolkata-700 016, India", gstin='19ACHFS9119B1Z0'),
    '29ACHFS9119B1ZZ': ClientRecord(entity_name='S R B C & ASSOCIATES LLP', address='Canberra block, 12th floor, No. 24, Vittal Mallya Road, UB City, Bengaluru (Bangalore) Urban, Karnataka, 560001', gstin='29ACHFS9119B1ZZ'),
    '06ACHFS9180N1ZD': ClientRecord(entity_name='S.R. Batliboi & Co. LLP', address='6th Floor, Plot No 67, Sector 44 Road, Institutional Area,Gurugram, Gurugram, Haryana, 122003', gstin='06ACHFS9180N1ZD'),
    '07ACHFS9180N1ZB': ClientRecord(entity_name='S.R. Batliboi & Co. LLP', address='4th Floor, Office 405 World Mark - 2, Asset No. 8 IGI Airport Hospitality District, Aerocity New Delhi-110037', gstin='07ACHFS9180N1ZB'),
    '27ACHFS9180N1Z9': ClientRecord(entity_name='S.R. Batliboi & Co. LLP', address='12th Floor, The Ruby, 29 Senapati Bapat Marg Dadar (West) Mumbai 400 028, India', gstin='27ACHFS9180N1Z9'),
    '04ACHFS9180N1ZH': ClientRecord(entity_name='S.R. Batliboi & Co. LLP', address='Elante offices, Unit No. B-615, 6th Floor, Plot No- 178-178A, Industrial & Business Park, Phase-I, Chandigarh – 160002, India', gstin='04ACHFS9180N1ZH'),
    '19ACHFS9180N1Z6': ClientRecord(entity_name='S.R. Batliboi & Co. LLP', address='22,3rd Floor,Camac Street,Block B,West bengal,Kolkata,700016', gstin='19ACHFS9180N1Z6'),
    '09ACHFS9180N1Z7': ClientRecord(entity_name='S.R. Batliboi & Co. LLP', address='Plot No. 2B,7th Floor, Tower 3,Sector 126,Uttar Pradesh, Gautam Buddha Nagar,201304', gstin='09ACHFS9180N1Z7'),
    '24ACHFS9180N1ZF': ClientRecord(entity_name='S.R. Batliboi & Co. LLP', address='B wing, 21st floor, Privilon, Ambli BRT Road, Behind Iskcon temple, Off SG Highway, Ahmedabad, Gujarat-380059, India', gstin='24ACHFS9180N1ZF'),
    '19ACHFS9181P1Z1': ClientRecord(entity_name='S.V. Ghatalia & Associates LLP', address='22,, 3rd Floor, Camac Street, Block B, Kolkata, West Bengal, 700016', gstin='19ACHFS9181P1Z1'),
    '27AALFP8393G1ZM': ClientRecord(entity_name='Lumiere Law Partners', address='2nd Floor, 23, 24 Mittal Chambers, Nariman Point, Mumbai, Mumbai, City, Maharashtra, 400021', gstin='27AALFP8393G1ZM'),
    '29AALFP8393G1ZI': ClientRecord(entity_name='Lumiere Law Partners', address="Divyasree Chambers, 7th floor, 'A' wing, # 11, O'Shaughnessy Road, Langford Town, Bengaluru, Karnataka - 560025, India", gstin='29AALFP8393G1ZI'),
    '09AALFP8393G1ZK': ClientRecord(entity_name='Lumiere Law Partners', address='3rd Floor, Plot No. 2B, Tower 2, Sector 126, Noida - 201304, Gautam Budh Nagar, Uttar Pradesh, India', gstin='09AALFP8393G1ZK'),
    '33AALFP8393G1ZT': ClientRecord(entity_name='Lumiere Law Partners', address='Eighth Floor, No.8-H, CENTURY PLAZA, No. 560, Anna Salai, Teynampet, Chennai, Chennai, Tamil Nadu, 600018', gstin='33AALFP8393G1ZT'),
    '27AALFP8393G2ZL': ClientRecord(entity_name='Lumiere Law Partners', address='2nd Floor, 23, 24 Mittal Chambers, Nariman Point, Mumbai, Mumbai, City, Maharashtra, 400021', gstin='27AALFP8393G2ZL'),
}


def lookup_ey_client(gstin: str, client_master=None) -> ClientRecord:
    try:
        return (client_master or EY_CLIENTS)[gstin.strip().upper()]
    except KeyError:
        raise UnknownClientError(f"EY GSTIN {gstin!r} is not in the EY client master. Add it in EEE-Taxi -> Masters -> Client entities.") from None


def ey_is_local(gstin: str) -> bool:
    return gstin.strip().startswith("06")


def ey_tax(taxable: Decimal, local: bool) -> tuple[Decimal, Decimal, Decimal]:
    if local:
        half = (taxable * Decimal("0.025")).quantize(Decimal("0.01"))
        return half, half, Decimal("0")
    return Decimal("0"), Decimal("0"), (taxable * Decimal("0.05")).quantize(Decimal("0.01"))
