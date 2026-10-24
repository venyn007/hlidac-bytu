import unittest
import watch

def flat(**kw):
    value=watch.item('sreality','https://example.org/1','Pronájem bytu 4+kk 120 m²',
                     'Havanská, Praha - Bubeneč',120,45000,'Dvě ložnice a samostatná pracovna.',
                     'Poplatky 2.000,-Kč + energie',50.1022,14.4184)
    value.update(kw)
    return value

class Filters(unittest.TestCase):
    def test_area_is_strict(self):
        self.assertFalse(watch.evaluate(flat(area=99.9)))
        self.assertTrue(watch.evaluate(flat(area=100)))

    def test_two_rooms_rejected(self):
        self.assertFalse(watch.evaluate(flat(title='Pronájem bytu 2+1 120 m²')))

    def test_unknown_costs_not_zero_or_full_match(self):
        e=flat(costs='Energie na nájemce')
        self.assertTrue(watch.evaluate(e))
        self.assertIn('k ověření',e['notes'][0])

    def test_service_cost_puts_flat_over_cap(self):
        self.assertFalse(watch.evaluate(flat(rent=64900,costs='Poplatky včetně energií 8 000 Kč')))
        self.assertFalse(watch.evaluate(flat(rent=65000,costs='Poplatky 6 500 Kč')))

    def test_commission_not_recurring(self):
        self.assertTrue(watch.evaluate(flat(costs='Kauce 90 000 Kč; provize 54 450 Kč; služby 2 000 Kč')))

    def test_services_and_energy_are_both_counted(self):
        self.assertFalse(watch.evaluate(flat(rent=60000,costs='Služby 7000 Kč; energie 4000 Kč')))

    def test_monthly_total_in_body_overrides_vague_note(self):
        self.assertFalse(watch.evaluate(flat(rent=64900,costs='+ poplatky',description='Celkové předpokládané měsíční náklady tak činí přibližně 72 900 Kč bez garáže.')))

    def test_unrelated_quarter_inside_bounding_box_rejected(self):
        self.assertFalse(watch.evaluate(flat(address='Haštalská, Praha, Staré Město',lat=50.095,lon=14.425)))

    def test_unknown_dejvice_street_not_expanded(self):
        self.assertFalse(watch.evaluate(flat(address='Studentská, Praha, Dejvice',lat=50.102,lon=14.395)))

    def test_outside_area_rejected(self):
        self.assertFalse(watch.evaluate(flat(address='V Háji, Praha',lat=50.105,lon=14.455)))

    def test_transit_mention_does_not_match_location(self):
        self.assertFalse(watch.evaluate(flat(address='Smíchov, Praha',description='Hradčanská za 10 minut',lat=None,lon=None)))

    def test_syndicated_duplicate_despite_price_change(self):
        e=flat(description='Dvě samostatné ložnice a zcela oddělená pracovna se nacházejí vedle obývacího pokoje.')
        stored={'identity':watch.identity(e),'shingles':list(watch.shingles(e)),'rent':45000}
        self.assertIsNotNone(watch.duplicate(flat(description=e['description'],rent=44000),[stored]))

    def test_same_street_and_size_not_enough_for_duplicate(self):
        e=flat(description='První jiný byt se samostatnými pokoji a novou kuchyní.')
        stored={'identity':watch.identity(e),'shingles':list(watch.shingles(e)),'rent':45000}
        self.assertIsNone(watch.duplicate(flat(description='Zcela odlišný mezonet nabízí nový nábytek a krásnou terasu.'),[stored]))

    def test_czech_number_formats(self):
        self.assertEqual(watch.amount('služby 2.000,-Kč'),2000)
        self.assertEqual(watch.amount('40\u200d 000\u200d Kč/měsíc'),40000)

if __name__=='__main__':
    unittest.main()
