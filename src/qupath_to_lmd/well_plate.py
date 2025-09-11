import string
import pandas

def create_list_of_acceptable_wells():
   list_of_acceptable_wells =[]
   for row in list(string.ascii_uppercase[2:14]):
      for column in range(3,22):
         list_of_acceptable_wells.append(str(row) + str(column))
   return list_of_acceptable_wells

def create_default_samples_and_wells(list_of_samples, list_of_acceptable_wells):
   assert len(list_of_samples) <= len(list_of_acceptable_wells), "Number of samples must be less than or equal to the number of wells"
   samples_and_wells = {}
   for sample,well in zip(list_of_samples, list_of_acceptable_wells):
      samples_and_wells[sample] = well
   return samples_and_wells

def sample_placement_384wp(samples_and_wells):

   rows_A_P= [i for i in string.ascii_uppercase[:16]]
   columns_1_24 = [str(i) for i in range(1,25)]
   df_wp384 = pandas.DataFrame('',columns=columns_1_24, index=rows_A_P)

   for i in samples_and_wells:
      location = samples_and_wells[i]
      df_wp384.at[location[0],location[1:]] = i

   return df_wp384
